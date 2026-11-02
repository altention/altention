"""A stand-in for a plugin's rewrite command (tests/test_rewrite.py): fake.py MODE [SECONDS] -o OUT, the reply on stdin."""
import json
import subprocess
import sys
import time

mode, out = sys.argv[1], sys.argv[sys.argv.index("-o") + 1]
text = sys.stdin.read()


def picture(n):
    path = out[:-4] + "-%d.png" % n
    with open(path, "wb") as f:
        f.write(b"\x89PNG fake %d" % n)
    return path


if mode == "slow":
    time.sleep(float(sys.argv[2]))
if mode in ("ok", "slow"):
    print(json.dumps([{"text": "前面的話", "images": [picture(1)]}, {"text": "", "images": [picture(2)]},
                      {"text": "後面的話", "images": []}], ensure_ascii=False))
elif mode == "sources":  # C-91: three pictures, the first two cut from one source, the third with a bad one
    pics, doc = [picture(1), picture(2), picture(3)], {"kind": "LaTeX 文件", "lang": "latex", "tool": "Tectonic",
                                                      "source": "\\section{a}"}
    print(json.dumps([{"text": "前面的話", "images": pics,
                       "sources": {pics[0]: doc, pics[1]: doc, pics[2]: {"kind": "表格"}, "/elsewhere.png": doc}},
                      {"text": "後面的話", "images": []}], ensure_ascii=False))
elif mode == "same":
    print(json.dumps([{"text": text, "images": []}], ensure_ascii=False))
elif mode == "fail":
    sys.stderr.write("這台沒有 Chrome\n")
    sys.exit(2)
elif mode == "hang":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with open(out + ".child", "w") as f:
        f.write(str(child.pid))
    time.sleep(60)
elif mode == "junk":
    print("not json")
elif mode == "elsewhere":
    print(json.dumps([{"text": "x", "images": [__file__]}]))
