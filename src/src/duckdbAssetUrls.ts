import duckdbWasmMvpUrl from '@duckdb/duckdb-wasm/dist/duckdb-mvp.wasm?url'
import duckdbWorkerMvpUrl from '@duckdb/duckdb-wasm/dist/duckdb-browser-mvp.worker.js?url'
import duckdbWasmEhUrl from '@duckdb/duckdb-wasm/dist/duckdb-eh.wasm?url'
import duckdbWorkerEhUrl from '@duckdb/duckdb-wasm/dist/duckdb-browser-eh.worker.js?url'
import type { DuckDBAssetUrls } from '@trilogy-data/trilogy-studio-components/connections'

export const DUCKDB_ASSET_URLS: DuckDBAssetUrls = {
  mvp: {
    mainModule: duckdbWasmMvpUrl,
    mainWorker: duckdbWorkerMvpUrl,
  },
  eh: {
    mainModule: duckdbWasmEhUrl,
    mainWorker: duckdbWorkerEhUrl,
  },
}
