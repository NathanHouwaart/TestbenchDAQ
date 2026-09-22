import {defineConfig} from "vite";

// Every portal page lives under /data/. Assets must stay at that stable root:
// a relative URL from /data/wentelteef/ would incorrectly become
// /data/wentelteef/assets/ and be handled as a SPA page by the gateway.
export default defineConfig({base: "/data/"});
