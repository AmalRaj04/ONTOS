.PHONY: hydradb-up hydradb-down venv

VENV := .venv/bin

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

venv:
	/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 -m venv .venv
	$(VENV)/pip install --quiet --upgrade pip
	$(VENV)/pip install --quiet -r requirements.txt
