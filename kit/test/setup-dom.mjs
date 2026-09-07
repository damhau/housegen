// Imported first by the tests: the minimal `document` three's ImageLoader needs at module
// scope, so the ez-tree build (which loads its textures on import) evaluates in Node.
if (typeof globalThis.document === "undefined") {
  globalThis.document = {
    createElementNS: () => ({ addEventListener() {}, removeEventListener() {}, style: {} }),
  };
}
