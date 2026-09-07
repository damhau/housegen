// Copy the kit's vendored dependencies from node_modules into kit/vendor/ (run by npm
// postinstall). runtime.js and house.js import them by relative path, so a scene's
// index.html only needs the "three", "three/addons/" and "housekit" importmap entries it
// has had since the first version: projects created before a dependency was added keep
// working, and the export zip ships the same files.
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const kit = join(dirname(fileURLToPath(import.meta.url)), "..");
const files = [
  ["node_modules/postprocessing/build/index.js", "vendor/postprocessing/index.js"],
  ["node_modules/n8ao/dist/N8AO.js", "vendor/n8ao/N8AO.js"],
  ["node_modules/@dgreenheck/ez-tree/build/ez-tree.es.js", "vendor/ez-tree/ez-tree.es.js"],
];
for (const [from, to] of files) {
  mkdirSync(join(kit, dirname(to)), { recursive: true });
  if (to.endsWith("N8AO.js")) {
    // n8ao imports three's Pass through "three/examples/jsm/…", which no scene importmap
    // maps; "three/addons/" is the same directory and every index.html has it
    const src = readFileSync(join(kit, from), "utf8");
    writeFileSync(join(kit, to), src.replaceAll('from "three/examples/jsm/', 'from "three/addons/'));
  } else {
    copyFileSync(join(kit, from), join(kit, to));
  }
}
console.log(`kit: vendored ${files.length} files under kit/vendor/`);
