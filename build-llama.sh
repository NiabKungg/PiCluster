#!/bin/bash
# Build llama.cpp on a Raspberry Pi. No sudo needed inside: run the
# apt/swap setup separately, then:  nohup ~/build-llama.sh &
set -e
exec > ~/llama-build.log 2>&1
echo "=== $(date) start build on $(hostname) ==="
rm -rf ~/llama.cpp
git clone --depth 1 https://github.com/ggml-org/llama.cpp ~/llama.cpp
cmake -S ~/llama.cpp -B ~/llama.cpp/build
cmake --build ~/llama.cpp/build --config Release -j2
echo "=== $(date) BUILD OK ==="
ls ~/llama.cpp/build/bin/ | head -20
