.PHONY: hydradb-up hydradb-down hydradb-minio-up hydradb-indexer-up venv

VENV := .venv/bin

# Override for wherever your MinIO-backed store/cache should live — the default
# assumes an external volume with real headroom (see PROJECT.md decisions #31/#34
# for why: full-corpus ingest needs ~90GB, and MinIO's own storage plus HydraDB's
# local cache both scale with data volume).
SSD_PATH ?= /Volumes/ONTOS_SSD
MINIO_PORT ?= 19000

# Native dev bring-up (see BUILD-SPEC.md §6 and docs/cypher-support.md).
# Uses CLOUD_PROVIDER=local — see PROJECT.md decision #6 for why this, not the
# MinIO-backed docker-compose.yml, is the day-to-day path.
hydradb-up:
	mkdir -p .hydradb/store .hydradb/cache
	grep '^HYDRADB_AUTH_TOKEN=' .env | cut -d'=' -f2- > .hydradb/auth-token
	cd vendor/hydradb && \
	CLOUD_PROVIDER=local \
	LOCAL_PATH="$(CURDIR)/.hydradb/store" \
	GRAPH_NAMESPACE=default GRAPH_ID=default GRAPH_CELL_ID=cell-0 GRAPH_CELLS=cell-0 \
	GRAPH_NODE_ID=node-0 GRAPH_BOLT_NODE_ADDRESSES=node-0=127.0.0.1:7687 \
	GRAPH_ADVERTISED_BOLT_ADDR=127.0.0.1:7687 \
	GRAPH_DATA_CACHE_DIR="$(CURDIR)/.hydradb/cache" \
	GRAPH_AUTH_TOKEN_FILE="$(CURDIR)/.hydradb/auth-token" \
	GRAPH_ALLOW_PLAINTEXT=true \
	RUST_MIN_STACK=33554432 \
	BINDGEN_EXTRA_CLANG_ARGS="-I/opt/homebrew/include" \
	LIBRARY_PATH="/opt/homebrew/lib" \
	nohup cargo run --locked --features server-runtime --bin graph-node \
		> "$(CURDIR)/.hydradb/graph-node.log" 2>&1 & echo $$! > "$(CURDIR)/.hydradb/graph-node.pid"
	@echo "graph-node starting; logs at .hydradb/graph-node.log, readyz at http://127.0.0.1:9090/readyz"

hydradb-down:
	-kill `cat .hydradb/graph-node.pid` 2>/dev/null
	rm -f .hydradb/graph-node.pid

# M2-scale bulk ingest path: MinIO-backed (fixes the local-filesystem GC bug, see
# PROJECT.md decision #30), larger cache, and a raised query-runtime budget (see
# decision #34 — legitimate large batch writes under sustained load exceed
# graph-node's 30s default). Start MinIO first (see docker-compose.yml for the
# container definition; run it standalone with a bucket named "ontos-graph" and a
# host port that doesn't collide with anything else on the machine — port 9000
# collided with a local php-fpm process on the build machine, hence MINIO_PORT).
hydradb-minio-up:
	mkdir -p "$(SSD_PATH)/hydradb-cache"
	grep '^HYDRADB_AUTH_TOKEN=' .env | cut -d'=' -f2- > .hydradb/auth-token
	cd vendor/hydradb && \
	CLOUD_PROVIDER=aws \
	AWS_BUCKET_NAME=ontos-graph \
	AWS_DEFAULT_REGION=us-east-1 \
	AWS_ALLOW_HTTP=true \
	AWS_ENDPOINT=http://127.0.0.1:$(MINIO_PORT) \
	AWS_ACCESS_KEY_ID=ontos-minio \
	AWS_SECRET_ACCESS_KEY=ontos-minio-secret \
	GRAPH_NAMESPACE=default GRAPH_ID=default GRAPH_CELL_ID=cell-0 GRAPH_CELLS=cell-0 \
	GRAPH_NODE_ID=node-0 GRAPH_BOLT_NODE_ADDRESSES=node-0=127.0.0.1:7687 \
	GRAPH_ADVERTISED_BOLT_ADDR=127.0.0.1:7687 \
	GRAPH_DATA_CACHE_DIR="$(SSD_PATH)/hydradb-cache" \
	GRAPH_DATA_CACHE_BYTES=536870912 \
	GRAPH_MAX_QUERY_RUNTIME_MS=240000 \
	GRAPH_AUTH_TOKEN_FILE="$(CURDIR)/.hydradb/auth-token" \
	GRAPH_ALLOW_PLAINTEXT=true \
	RUST_MIN_STACK=33554432 \
	BINDGEN_EXTRA_CLANG_ARGS="-I/opt/homebrew/include" \
	LIBRARY_PATH="/opt/homebrew/lib" \
	nohup cargo run --locked --features server-runtime --bin graph-node \
		> "$(CURDIR)/.hydradb/graph-node.log" 2>&1 & echo $$! > "$(CURDIR)/.hydradb/graph-node.pid"
	@echo "graph-node (MinIO-backed) starting; logs at .hydradb/graph-node.log"

# CSC index generations for edge-type traversal (algo.MSpaths etc, M3/M5). Not
# needed for M0/M1-scale work but should run alongside hydradb-minio-up for any
# real ingest or ER/multi-hop workload.
hydradb-indexer-up:
	cd vendor/hydradb && \
	CLOUD_PROVIDER=aws \
	AWS_BUCKET_NAME=ontos-graph \
	AWS_DEFAULT_REGION=us-east-1 \
	AWS_ALLOW_HTTP=true \
	AWS_ENDPOINT=http://127.0.0.1:$(MINIO_PORT) \
	AWS_ACCESS_KEY_ID=ontos-minio \
	AWS_SECRET_ACCESS_KEY=ontos-minio-secret \
	GRAPH_NAMESPACE=default GRAPH_ID=default GRAPH_CELLS=cell-0 \
	GRAPH_DATA_PATH=graph/data \
	RUST_MIN_STACK=33554432 \
	BINDGEN_EXTRA_CLANG_ARGS="-I/opt/homebrew/include" \
	LIBRARY_PATH="/opt/homebrew/lib" \
	nohup cargo run --locked --features indexer-runtime --bin graph-indexer \
		> "$(CURDIR)/.hydradb/graph-indexer.log" 2>&1 & echo $$! > "$(CURDIR)/.hydradb/graph-indexer.pid"
	@echo "graph-indexer starting; logs at .hydradb/graph-indexer.log"

venv:
	/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 -m venv .venv
	$(VENV)/pip install --quiet --upgrade pip
	$(VENV)/pip install --quiet -r requirements.txt
