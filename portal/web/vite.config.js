import {defineConfig} from "vite";

// The portal is served behind /MACHINE/machine-data/, not from a host root.
// Relative assets keep that public prefix intact.
export default defineConfig({base: "./"});
