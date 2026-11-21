const esbuild = require("esbuild");

const watch = process.argv.includes("--watch");

const options = {
    entryPoints: ["src/extension.ts"],
    bundle: true,
    outfile: "dist/extension.js",
    platform: "node",
    format: "cjs",
    external: ["vscode"],
    sourcemap: false
};

if (watch) {
    esbuild.context(options).then(ctx => ctx.watch());
} else {
    esbuild.build(options).catch(() => process.exit(1));
}
