#!/usr/bin/env python3
import hashlib, io, json, os, subprocess, sys, urllib.request, zipfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pack_factory as pf


def get(urls, sha):
    last = "no url"
    for u in urls:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "aot-packs"})
            data = urllib.request.urlopen(req, timeout=180).read()
            h = hashlib.sha256(data).hexdigest()
            if sha and h != sha:
                last = "sha256 mismatch for " + u
                continue
            return data, h, u
        except Exception as e:
            last = u + ": " + str(e)
    raise RuntimeError(last)


def place(work, f, lock):
    if not f.get("urls"):
        if f.get("optional"):
            return False
        raise RuntimeError("no url for " + f["dest"])
    data, h, u = get(f["urls"], f.get("sha256", ""))
    lock[f["dest"]] = {"url": u, "sha256": h}
    if f.get("zip_member"):
        data = zipfile.ZipFile(io.BytesIO(data)).read(f["zip_member"])
    elif f.get("aar_classes"):
        data = zipfile.ZipFile(io.BytesIO(data)).read("classes.jar")
    dest = os.path.join(work, f["dest"])
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as o:
        o.write(data)
    return True


def dex(work, spec, root):
    src = os.path.join(work, spec["src"])
    dst = os.path.join(work, spec["dst"])
    os.makedirs(dst, exist_ok=True)
    jars = sorted(os.path.join(src, n) for n in os.listdir(src) if n.endswith(".jar"))
    cmd = ["java", "-cp", os.path.join(root, "core", "r8", "r8.jar"), "com.android.tools.r8.D8", "--release", "--min-api", "26", "--lib", os.path.join(root, "core", "android.jar"), "--output", dst] + jars
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("d8 failed: " + r.stderr.decode(errors="replace")[-300:])


def main(src, out, repo):
    spec = json.load(open(src))
    os.makedirs(out, exist_ok=True)
    root = os.path.join(out, "work")
    lock = {}
    report = {"built": [], "failed": {}}
    for p in spec["packs"]:
        pid = p["id"]
        work = os.path.join(root, pid)
        try:
            os.makedirs(work, exist_ok=True)
            for f in p["files"]:
                place(work, f, lock)
            if p.get("dex"):
                dex(work, p["dex"], root)
            rest = []
            if p.get("requires"):
                rest += ["--requires", ",".join(p["requires"])]
            for k, v in p.get("entries", {}).items():
                if os.path.exists(os.path.join(work, v)):
                    rest += ["--entry", k + "=" + v]
            pf.build(work, out, pid, p["version"], p["name"], rest)
            report["built"].append(pid)
        except BaseException as e:
            report["failed"][pid] = str(e)[:400]
            for n in (pid + ".info.json",):
                if os.path.exists(os.path.join(out, n)):
                    os.remove(os.path.join(out, n))
    pf.body(out, "https://github.com/" + repo + "/releases/download/packs")
    json.dump(lock, open(os.path.join(out, "sources.lock.json"), "w"), indent=1)
    json.dump(report, open(os.path.join(out, "report.json"), "w"), indent=1)
    print(json.dumps(report, indent=1))
    if not report["built"]:
        sys.exit("no pack could be built")


main(sys.argv[1], sys.argv[2], sys.argv[3])
