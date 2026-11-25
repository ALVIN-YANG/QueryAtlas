#!/bin/sh
# Isolated Linux x86-64 host. Reuse completed data packs, never overwrite an existing source.
set -eu
rootdir="$HOME/queryatlas-bench"
mkdir -p "$rootdir"
cd "$rootdir"
checkout() {
  if ! test -d "$2/.git"; then git clone -q "$1" "$2"; git -C "$2" checkout -q "$3"; fi
  test "$(git -C "$2" rev-parse HEAD)" = "$3"
}
checkout https://github.com/StarRocks/BenchmarkTool.git upstream 0f9ce82ea978318e2830767ad27028659c7434fa
checkout https://github.com/vadimtk/ssb-dbgen.git ssbgen 0741e06d4c3e811bcec233378a39db2fc0be5d79
checkout https://github.com/StarRocks/tpcds-kit.git dsgen f26eb8d9f129849da8da0ba7510a1789f362a5d5
if ! test -d sf1; then (cd upstream && bash bin/gen_data/gen-tpch.sh 1 "$rootdir/sf1.pending"); mv sf1.pending sf1; fi
if ! test -d ssb-clean; then
  make -s -C ssbgen -j4
  mkdir ssb.pending
  (cd ssb.pending && ../ssbgen/dbgen -f -s 1 -T a -b ../ssbgen/dists.dss)
  mv ssb.pending ssb-clean
fi
if ! test -d ds1; then
  make -s -C dsgen/tools -j4 OS=LINUX LINUX_CFLAGS="-O2 -fcommon" dsdgen
  mkdir ds.pending
  (cd dsgen/tools && ./dsdgen -SCALE 1 -DIR "$rootdir/ds.pending")
  mv ds.pending ds1
fi
