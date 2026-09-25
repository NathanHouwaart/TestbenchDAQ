// Windows helper for the legacy PhotonFirst/Technobis Switched Gator API.
// It matches the Linux recorder's command line and CSV layout so Python
// orchestration remains platform-independent.

#include <switchedgator.h>

#include <array>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <limits>
#include <optional>
#include <stdexcept>
#include <string>
#include <thread>
#include <windows.h>

namespace {

constexpr uint32_t kRowsPerRead = 64;
constexpr uint32_t kColumnsPerRow = 14;
constexpr uint32_t kWavelengthScalePmToFm = 1000;
constexpr uint32_t kMaxThreshold = 16'777'215;
std::atomic<bool> g_stopRequested{false};

BOOL WINAPI consoleControlHandler(DWORD controlType)
{
  if (controlType == CTRL_C_EVENT || controlType == CTRL_BREAK_EVENT ||
      controlType == CTRL_CLOSE_EVENT || controlType == CTRL_SHUTDOWN_EVENT) {
    g_stopRequested.store(true);
    return TRUE;
  }
  return FALSE;
}

struct CliConfig {
  std::string outputPath = "channel_samples.csv";
  uint64_t durationSeconds = 10;
  int channelNumber = 8;
  std::optional<uint8_t> fullScaleRange;
  std::optional<double> detectionThreshold;
  std::optional<int> sampleRateHz;
  std::optional<size_t> deviceIndex;
};

[[noreturn]] void usageAndExit(int code)
{
  std::cerr
      << "Usage: gator_recorder [OPTIONS]\n\n"
      << "  --output PATH       Output CSV file path       (default: channel_samples.csv)\n"
      << "  --duration-s N      Record for N seconds, 0 = until Ctrl+Break (default: 10)\n"
      << "  --channel N         Channel 1-8                (default: 8)\n"
      << "  --device-index 0    Legacy API supports one Gator only\n"
      << "  --fullscale N       Full-scale range 8-127     (default: device setting)\n"
      << "  --threshold F       Detection threshold 0-1    (default: device setting)\n"
      << "  --samplerate NAME   1000|5000|10000|19000      (default: device setting)\n";
  std::exit(code);
}

CliConfig parseCli(int argc, char* argv[])
{
  CliConfig config;
  for (int index = 1; index < argc; ++index) {
    const std::string key = argv[index];
    if (key == "--help" || key == "-h") usageAndExit(0);
    auto nextArg = [&]() -> std::string {
      if (++index >= argc) throw std::runtime_error("Missing value for " + key);
      return argv[index];
    };
    if (key == "--output") config.outputPath = nextArg();
    else if (key == "--duration-s") {
      config.durationSeconds = std::stoull(nextArg());
      if (config.durationSeconds > std::numeric_limits<uint64_t>::max() / 1'000'000ULL)
        throw std::runtime_error("--duration-s is too large");
    } else if (key == "--channel") {
      config.channelNumber = std::stoi(nextArg());
      if (config.channelNumber < 1 || config.channelNumber > 8)
        throw std::runtime_error("--channel must be 1-8");
    } else if (key == "--device-index") {
      config.deviceIndex = std::stoull(nextArg());
      if (*config.deviceIndex != 0)
        throw std::runtime_error("legacy Windows API supports only --device-index 0");
    } else if (key == "--fullscale") {
      const int value = std::stoi(nextArg());
      if (value < 8 || value > 127) throw std::runtime_error("--fullscale must be 8-127");
      config.fullScaleRange = static_cast<uint8_t>(value);
    } else if (key == "--threshold") {
      const double value = std::stod(nextArg());
      if (value < 0.0 || value > 1.0) throw std::runtime_error("--threshold must be 0.0-1.0");
      config.detectionThreshold = value;
    } else if (key == "--samplerate") {
      const int value = std::stoi(nextArg());
      if (value != 1000 && value != 5000 && value != 10000 && value != 19000)
        throw std::runtime_error("--samplerate must be 1000, 5000, 10000 or 19000");
      config.sampleRateHz = value;
    } else throw std::runtime_error("Unknown option: " + key);
  }
  return config;
}

uint8_t sampleRateIndex(int requestedHz)
{
  switch (requestedHz) {
    case 1000: return 1;
    case 5000: return 2;
    case 10000: return 3;
    case 19000: return 4; // The legacy API reports approximately 19.23 kHz.
    default: throw std::runtime_error("Unsupported sample rate");
  }
}

uint64_t nowUtcUs()
{
  return static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::microseconds>(
      std::chrono::system_clock::now().time_since_epoch()).count());
}

void requireSuccess(int result, const char* operation)
{
  if (!result) throw std::runtime_error(std::string(operation) + " failed");
}

class GatorConnection {
 public:
  GatorConnection()
  {
    if (!isAttached()) throw std::runtime_error("No switched Gator detected.");
    requireSuccess(connect_SG(&_handle), "connect_SG");
    _connected = true;
  }
  ~GatorConnection()
  {
    if (_streaming) stopDataStream(&_handle);
    if (_connected) closePort(&_handle);
  }
  SG_HANDLE* handle() { return &_handle; }
  void markStreaming() { _streaming = true; }
  void stopStream()
  {
    if (_streaming) {
      requireSuccess(stopDataStream(&_handle), "stopDataStream");
      _streaming = false;
    }
  }
  void close()
  {
    if (_connected) {
      requireSuccess(closePort(&_handle), "closePort");
      _connected = false;
    }
  }

 private:
  SG_HANDLE _handle = nullptr;
  bool _connected = false;
  bool _streaming = false;
};

} // namespace

int main(int argc, char* argv[])
{
  SetConsoleCtrlHandler(consoleControlHandler, TRUE);
  try {
    const CliConfig config = parseCli(argc, argv);
    GatorConnection gator;
    requireSuccess(setChannelRange(gator.handle(), config.channelNumber, config.channelNumber),
                   "setChannelRange");
    if (config.fullScaleRange)
      requireSuccess(setFullScaleRange(*config.fullScaleRange), "setFullScaleRange");
    if (config.detectionThreshold) {
      const auto threshold = static_cast<uint32_t>(std::llround(
          *config.detectionThreshold * static_cast<double>(kMaxThreshold)));
      requireSuccess(setThreshold(threshold), "setThreshold");
    }

    double actualSampleRateHz = get_sampling_rate();
    const int requestedSampleRateHz = config.sampleRateHz.value_or(
        static_cast<int>(std::llround(actualSampleRateHz)));
    if (config.sampleRateHz) {
      actualSampleRateHz = set_sampling_rate(sampleRateIndex(*config.sampleRateHz));
      if (actualSampleRateHz <= 0.0) throw std::runtime_error("set_sampling_rate failed");
    }
    if (actualSampleRateHz <= 0.0)
      throw std::runtime_error("get_sampling_rate returned an invalid value");

    requireSuccess(purgeBuffer(gator.handle()), "purgeBuffer");
    std::ofstream output(config.outputPath);
    if (!output) throw std::runtime_error("Failed to open output file: " + config.outputPath);
    output << "utc_timestamp_us,channel,"
              "sensor_1_fm,sensor_2_fm,sensor_3_fm,sensor_4_fm,"
              "sensor_5_fm,sensor_6_fm,sensor_7_fm,sensor_8_fm\n";

    requireSuccess(startDataStream(gator.handle()), "startDataStream");
    gator.markStreaming();
    std::cout << "GATOR_METADATA={\"device_index\":0,\"requested_samplerate_hz\":"
              << requestedSampleRateHz << ",\"actual_samplerate_hz\":" << actualSampleRateHz
              << ",\"timestamp_source\":\"host_estimated\",\"vendor_api\":\"gatorapi-3.3.0\"}\n";
    std::cout.flush();

    const auto startedAt = std::chrono::steady_clock::now();
    std::array<std::array<uint32_t, kColumnsPerRow>, kRowsPerRead> rows{};
    uint64_t samplesWritten = 0;
    uint64_t lastTimestampUs = 0;
    bool announcedStart = false;
    while (!g_stopRequested.load()) {
      if (config.durationSeconds > 0 &&
          std::chrono::steady_clock::now() - startedAt >= std::chrono::seconds(config.durationSeconds)) break;
      const int count = readDataStreamFixedLen(gator.handle(),
          reinterpret_cast<uint32_t (*)[14]>(rows.data()), kRowsPerRead, true);
      if (count < 0 || count > static_cast<int>(kRowsPerRead))
        throw std::runtime_error("readDataStreamFixedLen returned an invalid sample count");
      if (count == 0) {
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
        continue;
      }

      // Legacy timestamps are device-relative, not UTC. Map each wavelength
      // batch to host UTC at receipt time; the metadata records this limitation.
      const uint64_t batchEndUtcUs = nowUtcUs();
      const uint64_t batchSpanUs = static_cast<uint64_t>(std::llround(
          static_cast<double>(count - 1) * 1'000'000.0 / actualSampleRateHz));
      uint64_t timestampUs = batchEndUtcUs > batchSpanUs ? batchEndUtcUs - batchSpanUs : 0;
      for (int rowIndex = 0; rowIndex < count; ++rowIndex) {
        const auto& row = rows[static_cast<size_t>(rowIndex)];
        if (!announcedStart) {
          std::cout << "START_UTC_US=" << timestampUs << '\n';
          std::cout.flush();
          announcedStart = true;
        }
        output << timestampUs << ',' << config.channelNumber;
        for (size_t sensor = 6; sensor < 14; ++sensor)
          output << ',' << static_cast<uint64_t>(row[sensor]) * kWavelengthScalePmToFm;
        output << '\n';
        lastTimestampUs = timestampUs;
        ++samplesWritten;
        timestampUs += static_cast<uint64_t>(std::llround(1'000'000.0 / actualSampleRateHz));
      }
      if (!output) throw std::runtime_error("Failed while writing CSV output");
    }

    gator.stopStream();
    output.flush();
    if (!output) throw std::runtime_error("Failed to flush CSV output");
    if (samplesWritten == 0)
      throw std::runtime_error("Gator recording completed without receiving any samples");
    std::cout << "LAST_SAMPLE_UTC_US=" << lastTimestampUs << '\n';
    std::cout << "Wrote " << samplesWritten << " samples to " << config.outputPath << '\n';
    std::cout.flush();
    gator.close();
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
