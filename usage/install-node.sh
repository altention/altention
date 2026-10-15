#!/bin/sh
# Node and ccusage for this user, without root: ~/.local/opt/node-<version>-<os>-<arch>/ (ccusage reads the
# local Claude Code and Codex logs and prices them like the API). Safe to run again.
# The .tar.gz build (every platform has one; .tar.xz needs xz, which minimal systems lack), Linux or macOS.
set -e
V=v22.12.0
case "$(uname -s)" in Linux) O=linux ;; Darwin) O=darwin ;; *) echo "不支援的系統 $(uname -s)（只支援 Linux 和 macOS）"; exit 1 ;; esac
case "$(uname -m)" in aarch64|arm64) A=arm64 ;; x86_64) A=x64 ;; *) echo "不支援的架構 $(uname -m)"; exit 1 ;; esac
D="$HOME/.local/opt/node-$V-$O-$A"
if [ ! -x "$D/bin/node" ]; then
  mkdir -p "$HOME/.local/opt"
  U="https://nodejs.org/dist/$V/node-$V-$O-$A.tar.gz"
  T="$HOME/.local/opt/.node-$V-$O-$A.tar.gz.part"
  curl -fsSL -o "$T" "$U" || { rm -f "$T"; echo "下載 node 失敗：$U（網路連得到 nodejs.org 嗎？）"; exit 1; }
  tar xzf "$T" -C "$HOME/.local/opt" || { rm -f "$T"; echo "解開 node 失敗：tar xzf（需要 tar 和 gzip）"; exit 1; }
  rm -f "$T"
fi
if [ ! -x "$D/bin/ccusage" ]; then
  PATH="$D/bin:$PATH" npm install -g --silent ccusage@20 >/dev/null 2>&1 || { echo "裝 ccusage 失敗：PATH=$D/bin:\$PATH npm install -g ccusage@20 看錯誤"; exit 1; }
fi
echo "node $("$D/bin/node" -v)，ccusage $(PATH="$D/bin:$PATH" "$D/bin/ccusage" --version 2>/dev/null)"
