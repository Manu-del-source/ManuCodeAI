import test from "node:test";
import assert from "node:assert/strict";
import * as fs from "fs/promises";
import * as path from "path";
import { DEFAULT_TYPING_SPEED, TYPING_SPEED_CPS } from "../src/core/typewriter";

/**
 * Guard the public settings contract: the contributed `manucodeai.typingSpeed`
 * options must stay in sync with the code's speed table, so the documented
 * characters-per-second values are what actually gets applied.
 */
test("package.json contributes the three typing-speed options with the documented default", async () => {
    const raw = await fs.readFile(path.join(process.cwd(), "package.json"), "utf8");
    const pkg = JSON.parse(raw);

    const setting = pkg?.contributes?.configuration?.properties?.["manucodeai.typingSpeed"];
    assert.ok(setting, "manucodeai.typingSpeed must be contributed in package.json");

    assert.deepEqual(setting.enum, ["slow", "normal", "fast"]);
    assert.deepEqual(setting.enum, Object.keys(TYPING_SPEED_CPS));
    assert.equal(setting.default, DEFAULT_TYPING_SPEED);
    assert.equal(setting.default, "normal");

    assert.equal(setting.enumDescriptions.length, setting.enum.length);
    const joined = setting.enumDescriptions.join(" ");
    for (const cps of Object.values(TYPING_SPEED_CPS)) {
        assert.ok(joined.includes(`${cps} characters per second`), `mentions ${cps} cps`);
    }
});
