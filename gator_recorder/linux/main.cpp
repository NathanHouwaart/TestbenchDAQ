/// recordchannel8csv — stream a single Gator channel to CSV with bounded memory.
/// Send SIGTERM or SIGINT to stop early (remaining buffer is flushed to disk).

#include <gtrlib/GTRLib.h>

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <csignal>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <limits>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

namespace {

std::atomic<bool> g_stopRequested{false};

extern "C" void signalHandler(int) { g_stopRequested.store(true); }

struct CsvRow {
  uint64_t utcTimestampUs;
  P1::Channel channel;
  uint32_t wavelengthFm[8];
};

class BufferedCsvWriter {
 public:
  static constexpr size_t kBufferCapacity = 4096;

  explicit BufferedCsvWriter(const std::string& outputPath)
      : _output(outputPath), _activeBuffer(&_bufferA), _writeBuffer(&_bufferB)
  {
    if (!_output) throw std::runtime_error("Failed to open output file: " + outputPath);
    _bufferA.reserve(kBufferCapacity);
    _bufferB.reserve(kBufferCapacity);
    _output << "utc_timestamp_us,channel,"
               "sensor_1_fm,sensor_2_fm,sensor_3_fm,sensor_4_fm,"
               "sensor_5_fm,sensor_6_fm,sensor_7_fm,sensor_8_fm\n";
    _writerThread = std::thread([this] { writerLoop(); });
  }

  ~BufferedCsvWriter() { try { close(); } catch (...) {} }

  void append(const CsvRow& row)
  {
    std::unique_lock<std::mutex> lock(_mutex);
    rethrowIfWriteFailedLocked();
    _activeBuffer->push_back(row);
    if (_activeBuffer->size() >= kBufferCapacity) flushActiveBufferLocked(lock);
  }

  void close()
  {
    std::unique_lock<std::mutex> lock(_mutex);
    if (_closed) { rethrowIfWriteFailedLocked(); return; }
    if (!_activeBuffer->empty()) flushActiveBufferLocked(lock);
    _stopRequested = true;
    _flushRequested.notify_one();
    lock.unlock();
    if (_writerThread.joinable()) _writerThread.join();
    lock.lock();
    _output.flush();
    if (!_output) {
      _writeFailed = true;
      _writeError = "Failed to flush CSV output.";
    }
    _closed = true;
    rethrowIfWriteFailedLocked();
  }

 private:
  void flushActiveBufferLocked(std::unique_lock<std::mutex>& lock)
  {
    _bufferAvailable.wait(lock, [this] { return !_writePending; });
    std::swap(_activeBuffer, _writeBuffer);
    _writePending = true;
    _flushRequested.notify_one();
  }

  void writerLoop()
  {
    std::vector<CsvRow> local;
    local.reserve(kBufferCapacity);
    for (;;) {
      std::unique_lock<std::mutex> lock(_mutex);
      _flushRequested.wait(lock, [this] { return _writePending || _stopRequested; });
      if (!_writePending && _stopRequested) return;
      std::swap(local, *_writeBuffer);
      _writePending = false;
      _bufferAvailable.notify_one();
      lock.unlock();
      for (const auto& row : local) {
        _output << row.utcTimestampUs << ',' << static_cast<int>(row.channel) + 1;
        for (const auto wl : row.wavelengthFm) _output << ',' << wl;
        _output << '\n';
      }
      if (!_output) {
        std::lock_guard<std::mutex> el(_mutex);
        _writeFailed = true; _writeError = "Write error on CSV output.";
        _stopRequested = true; _bufferAvailable.notify_one();
        return;
      }
      local.clear();
    }
  }

  void rethrowIfWriteFailedLocked() const
  { if (_writeFailed) throw std::runtime_error(_writeError); }

  std::ofstream _output;
  std::thread _writerThread;
  mutable std::mutex _mutex;
  std::condition_variable _flushRequested;
  std::condition_variable _bufferAvailable;
  std::vector<CsvRow> _bufferA, _bufferB;
  std::vector<CsvRow>* _activeBuffer;
  std::vector<CsvRow>* _writeBuffer;
  bool _writePending = false, _stopRequested = false, _closed = false, _writeFailed = false;
  std::string _writeError;
};

class ChannelRecorder {
 public:
  ChannelRecorder(P1::Channel ch, uint64_t durationUs, BufferedCsvWriter& writer)
      : _channel(ch), _captureDurationUs(durationUs), _writer(writer) {}

  void onSample(const P1::Sample& sample)
  {
    try {
      if (_finished.load() || g_stopRequested.load()) {
        if (!_finished.exchange(true)) _done.notify_one();
        return;
      }
      if (sample.channel != _channel) return;
      std::lock_guard<std::mutex> lock(_mutex);
      if (_finished.load()) return;
      if (_startUtcUs == 0) {
        _startUtcUs = sample.utcTimestampUs;
        std::cout << "START_UTC_US=" << _startUtcUs << '\n';
        std::cout.flush();
      }
      if (_captureDurationUs > 0 &&
          sample.utcTimestampUs - _startUtcUs >= _captureDurationUs) {
        _finished.store(true); _done.notify_one(); return;
      }
      _writer.append(CsvRow{
          sample.utcTimestampUs, sample.channel,
          {sample.wavelengthFm[P1::Channel1], sample.wavelengthFm[P1::Channel2],
           sample.wavelengthFm[P1::Channel3], sample.wavelengthFm[P1::Channel4],
           sample.wavelengthFm[P1::Channel5], sample.wavelengthFm[P1::Channel6],
           sample.wavelengthFm[P1::Channel7], sample.wavelengthFm[P1::Channel8]}});
      _lastUtcUs = sample.utcTimestampUs;
      _sampleCount++;
    } catch (const std::exception& ex) {
      fail(std::string("Sample callback failed: ") + ex.what());
    } catch (...) {
      fail("Sample callback failed with an unknown exception.");
    }
  }

  void waitUntilFinished()
  {
    std::unique_lock<std::mutex> lock(_mutex);
    if (_captureDurationUs > 0) {
      auto timeout = std::chrono::microseconds(_captureDurationUs)
          + std::chrono::seconds(5);
      _done.wait_for(lock, timeout,
          [this] { return _finished.load() || g_stopRequested.load(); });
    } else {
      // A signal handler may safely set the atomic flag, but it may not safely
      // notify a condition_variable. Poll at a short interval so SIGTERM always
      // releases a manual-duration recording instead of relying on a spurious
      // wake-up from what used to be a year-long wait.
      while (!_finished.load() && !g_stopRequested.load()) {
        _done.wait_for(lock, std::chrono::milliseconds(100));
      }
    }
    _finished.store(true);
  }

  size_t sampleCount() const { return _sampleCount.load(); }
  uint64_t lastUtcUs() const
  {
    std::lock_guard<std::mutex> lock(_mutex);
    return _lastUtcUs;
  }
  std::optional<std::string> failure() const
  {
    std::lock_guard<std::mutex> lock(_mutex);
    return _failure;
  }

 private:
  P1::Channel _channel;
  uint64_t _captureDurationUs;
  BufferedCsvWriter& _writer;
  mutable std::mutex _mutex;
  std::condition_variable _done;
  uint64_t _startUtcUs = 0;
  uint64_t _lastUtcUs = 0;
  std::atomic<bool> _finished{false};
  std::atomic<size_t> _sampleCount{0};
  std::optional<std::string> _failure;

  void fail(std::string message)
  {
    std::lock_guard<std::mutex> lock(_mutex);
    if (!_failure) _failure = std::move(message);
    _finished.store(true);
    g_stopRequested.store(true);
    _done.notify_one();
  }
};

struct CliConfig {
  std::string outputPath      = "channel_samples.csv";
  uint64_t    durationSeconds = 10;
  int         channelNumber   = 8;
  std::optional<size_t>        deviceIndex;
  std::optional<uint8_t>        fullScaleRange;
  std::optional<double>         detectionThreshold;
  std::optional<P1::SampleRate> sampleRate;
};

[[noreturn]] void printUsageAndExit(int code)
{
  std::cerr <<
    "Usage: recordchannel8csv [OPTIONS]\n\n"
    "  --output PATH       Output CSV file path       (default: channel_samples.csv)\n"
    "  --duration-s N      Record for N seconds, 0 = until SIGTERM  (default: 10)\n"
    "  --channel N         Channel 1-8                (default: 8)\n"
    "  --device-index N    Zero-based detected Gator index (required if multiple)\n"
    "  --fullscale N       Full-scale range 8-127     (default: from device)\n"
    "  --threshold F       Detection threshold 0-1    (default: from device)\n"
    "  --samplerate NAME   1000|5000|10000|19000      (default: from device)\n"
    "  --help              Print this message\n";
  std::exit(code);
}

CliConfig parseCli(int argc, char* argv[])
{
  CliConfig cfg;
  for (int i = 1; i < argc; ++i) {
    std::string key = argv[i];
    if (key == "--help" || key == "-h") printUsageAndExit(0);

    auto nextArg = [&]() -> std::string {
      if (++i >= argc) { std::cerr << "Missing value for " << key << '\n'; printUsageAndExit(1); }
      return argv[i];
    };

    if      (key == "--output")     cfg.outputPath      = nextArg();
    else if (key == "--duration-s") {
      cfg.durationSeconds = std::stoull(nextArg());
      if (cfg.durationSeconds > std::numeric_limits<uint64_t>::max() / 1'000'000ULL)
      { std::cerr << "--duration-s is too large\n"; printUsageAndExit(1); }
    }
    else if (key == "--device-index") cfg.deviceIndex = std::stoull(nextArg());
    else if (key == "--channel") {
      cfg.channelNumber = std::stoi(nextArg());
      if (cfg.channelNumber < 1 || cfg.channelNumber > 8)
      { std::cerr << "--channel must be 1-8\n"; printUsageAndExit(1); }
    }
    else if (key == "--fullscale") {
      int v = std::stoi(nextArg());
      if (v < P1::GatorConfig::kMinFullScaleRange || v > P1::GatorConfig::kMaxFullScaleRange)
      { std::cerr << "--fullscale must be 8-127\n"; printUsageAndExit(1); }
      cfg.fullScaleRange = static_cast<uint8_t>(v);
    }
    else if (key == "--threshold") {
      double v = std::stod(nextArg());
      if (v < 0.0 || v > 1.0) { std::cerr << "--threshold must be 0.0-1.0\n"; printUsageAndExit(1); }
      cfg.detectionThreshold = v;
    }
    else if (key == "--samplerate") {
      std::string v = nextArg();
      if      (v == "1000")  cfg.sampleRate = P1::SampleRate::k1000Hz;
      else if (v == "5000")  cfg.sampleRate = P1::SampleRate::k5000Hz;
      else if (v == "10000") cfg.sampleRate = P1::SampleRate::k10000Hz;
      else if (v == "19000") cfg.sampleRate = P1::SampleRate::k19000Hz;
      else { std::cerr << "--samplerate must be 1000, 5000, 10000 or 19000\n"; printUsageAndExit(1); }
    }
    else { std::cerr << "Unknown option: " << key << '\n'; printUsageAndExit(1); }
  }
  return cfg;
}

int sampleRateLabelHz(P1::SampleRate rate)
{
  switch (rate) {
    case P1::SampleRate::k1000Hz: return 1000;
    case P1::SampleRate::k5000Hz: return 5000;
    case P1::SampleRate::k10000Hz: return 10000;
    case P1::SampleRate::k19000Hz: return 19000;
  }
  return -1;
}

[[noreturn]] void exitAfterSubscription(int code, const std::string& message)
{
  std::cerr << message << '\n';
  std::cerr.flush();
  // GTRLib v0.1.0 exposes subscribe() but no unsubscribe/disconnect operation.
  // Do not enter vendor teardown after a subscription; it can hang.
  std::_Exit(code);
}

} // namespace

int main(int argc, char* argv[])
{
  std::signal(SIGTERM, signalHandler);
  std::signal(SIGINT,  signalHandler);

  CliConfig cfg;
  try { cfg = parseCli(argc, argv); }
  catch (const std::exception& ex) { std::cerr << "Argument error: " << ex.what() << '\n'; return 1; }

  P1::GTRLib gtrLib;

  const auto& devices = gtrLib.getDevices();
  if (devices.empty()) { std::cerr << "No gators detected!\n"; return 1; }

  if (!cfg.deviceIndex && devices.size() != 1) {
    std::cerr << "Detected " << devices.size()
              << " Gators; pass --device-index explicitly:\n";
    for (size_t index = 0; index < devices.size(); ++index) {
      std::cerr << "  [" << index << "] " << devices[index].info() << '\n';
    }
    return 2;
  }
  const size_t deviceIndex = cfg.deviceIndex.value_or(0);
  if (deviceIndex >= devices.size()) {
    std::cerr << "--device-index " << deviceIndex << " is out of range; detected "
              << devices.size() << " Gator(s).\n";
    return 2;
  }

  auto switchedGator = gtrLib.connectToSwitchedGator(devices[deviceIndex]);
  if (!switchedGator) { std::cerr << "Failed to connect to selected switched gator!\n"; return 3; }

  P1::GatorConfig gatorConfig;
  if (!switchedGator->getConfiguration(gatorConfig))
  { std::cerr << "Failed to read gator configuration!\n"; return 4; }

  bool changed = false;
  if (cfg.sampleRate)         { gatorConfig.sampleRate         = *cfg.sampleRate;         changed = true; }
  if (cfg.fullScaleRange)     { gatorConfig.fullScaleRange      = *cfg.fullScaleRange;     changed = true; }
  if (cfg.detectionThreshold) { gatorConfig.detectionThreshold  = *cfg.detectionThreshold; changed = true; }

  if (changed) {
    std::cout << "Applying gator configuration (may take up to 2s)...\n";
    if (!switchedGator->configure(gatorConfig))
    { std::cerr << "Failed to apply gator configuration!\n"; return 5; }
  }

  auto channel = static_cast<P1::Channel>(cfg.channelNumber - 1);
  P1::SwitchedGatorChannelConfig channelConfig;
  channelConfig.setSingleChannel(channel);
  if (!switchedGator->setChannelConfig(channelConfig))
  { std::cerr << "Failed to set channel config!\n"; return 6; }

  if (cfg.durationSeconds == 0)
    std::cout << "Recording channel " << cfg.channelNumber
              << " until SIGTERM/SIGINT to " << cfg.outputPath << "...\n";
  else
    std::cout << "Recording channel " << cfg.channelNumber
              << " for " << cfg.durationSeconds << " s to " << cfg.outputPath << "...\n";

  const int requestedSampleRateHz = sampleRateLabelHz(gatorConfig.sampleRate);
  const double actualSampleRateHz = switchedGator->getRealSamplingRateHz();
  std::cout << "GATOR_METADATA={\"device_index\":" << deviceIndex
            << ",\"requested_samplerate_hz\":" << requestedSampleRateHz
            << ",\"actual_samplerate_hz\":" << actualSampleRateHz
            << ",\"timestamp_source\":\"device_utc\""
            << ",\"vendor_api\":\"gtrlib-v0.1.0\"}\n";
  std::cout.flush();

  BufferedCsvWriter writer(cfg.outputPath);
  ChannelRecorder recorder(channel, cfg.durationSeconds * 1'000'000ULL, writer);

  switchedGator->subscribe(
      [&recorder](const P1::Sample& sample) { recorder.onSample(sample); });

  recorder.waitUntilFinished();

  try { writer.close(); }
  catch (const std::exception& ex) { exitAfterSubscription(7, ex.what()); }
  if (const auto failure = recorder.failure()) {
    exitAfterSubscription(8, *failure);
  }
  if (recorder.sampleCount() == 0) {
    exitAfterSubscription(9, "Gator recording completed without receiving any samples.");
  }

  std::cout << "LAST_SAMPLE_UTC_US=" << recorder.lastUtcUs() << '\n';
  std::cout << "Wrote " << recorder.sampleCount() << " samples to " << cfg.outputPath << '\n';
  std::cout.flush();

  // Output is explicitly flushed before bypassing vendor teardown. The OS
  // closes remaining USB handles when the process exits.
  std::_Exit(0);
}
