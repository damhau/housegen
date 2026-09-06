import { defineConfig } from "orval"

export default defineConfig({
  api: {
    input: { target: "./openapi.json" },
    output: {
      mode: "tags-split",
      target: "src/api/endpoints",
      schemas: "src/api/model",
      client: "react-query",
      httpClient: "fetch",
      // paths in openapi.json already carry the /api/v1 prefix; the dev proxy / nginx route them
      clean: true,
      override: {
        query: { useQuery: true, useMutation: true, signal: true },
        mutator: { path: "src/api/http-client.ts", name: "httpClient" },
        // the mutator returns the parsed body, not a {data,status,headers} envelope
        fetch: { includeHttpResponseReturnType: false },
      },
    },
  },
})
