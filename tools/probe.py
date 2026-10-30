import io, json, os, struct, sys, tarfile, traceback, urllib.request, hashlib, zipfile

OUT = sys.argv[1] if len(sys.argv) > 1 else "out"
from elfcheck import elf, SYS
TERMUX = "https://packages.termux.dev/apt/termux-main/"
res = {"errors": [], "lzhiyong": {}, "termux": {}}


def get(url, limit=300 * 1024 * 1024):
    h = {"User-Agent": "aot-probe"}
    tok = os.environ.get("GH_TOKEN")
    if tok and "api.github.com" in url:
        h["Authorization"] = "Bearer " + tok
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=120) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError("too large: " + url)
    return data


def err(where):
    res["errors"].append(where + ": " + traceback.format_exc().strip().splitlines()[-1])


def ar_data(deb):
    pos = 8
    while pos + 60 <= len(deb):
        name = deb[pos:pos + 16].decode().strip()
        size = int(deb[pos + 48:pos + 58].decode().strip())
        body = deb[pos + 60:pos + 60 + size]
        if name.startswith("data.tar"):
            return body, name
        pos += 60 + size + (size % 2)
    raise RuntimeError("no data.tar in deb")


def scan_tar(tf, keep):
    files, elfs, provides = 0, [], []
    for m in tf:
        base = os.path.basename(m.name)
        if m.issym() or m.islnk():
            if base.startswith("lib") and ".so" in base:
                provides.append(base)
            continue
        if not m.isfile():
            continue
        files += 1
        if base.startswith("lib") and ".so" in base:
            provides.append(base)
        f = tf.extractfile(m)
        head = f.read(4)
        if head == b"\x7fELF":
            e = elf(head + f.read())
            if e is not None and keep(m.name):
                e["path"] = m.name
                e["size"] = m.size
                elfs.append(e)
    return files, elfs, provides


def termux_index():
    txt = get(TERMUX + "dists/stable/main/binary-aarch64/Packages").decode(errors="replace")
    pk = {}
    for block in txt.split("\n\n"):
        d = {}
        for line in block.splitlines():
            if ": " in line and not line.startswith(" "):
                k, v = line.split(": ", 1)
                d[k] = v
        if "Package" in d:
            pk[d["Package"]] = d
    return pk


def closure(pk, root):
    seen, todo = [], [root]
    while todo:
        n = todo.pop()
        if n in seen or n not in pk:
            continue
        seen.append(n)
        for part in pk[n].get("Depends", "").split(","):
            nm = part.split("|")[0].split("(")[0].strip()
            if nm:
                todo.append(nm)
    return seen


def probe_termux():
    pk = termux_index()
    t = res["termux"]
    wanted = {}
    for root in ("aapt2", "openjdk-17"):
        if root not in pk:
            t[root] = {"missing": True}
            continue
        names = closure(pk, root)
        t[root] = {"version": pk[root].get("Version"), "filename": pk[root].get("Filename"), "sha256": pk[root].get("SHA256"), "size": pk[root].get("Size"), "depends": [{"name": n, "version": pk[n].get("Version"), "size": pk[n].get("Size")} for n in names]}
        for n in names:
            wanted[n] = pk[n]
    provided, binaries = {}, []
    t["packages"] = {}
    for n, d in wanted.items():
        if int(d.get("Size", "0")) > 160 * 1024 * 1024:
            t["packages"][n] = {"skipped": "too large"}
            continue
        try:
            raw = get(TERMUX + d["Filename"])
            body, nm = ar_data(raw)
            mode = "r:xz" if nm.endswith("xz") else "r:gz" if nm.endswith("gz") else "r:*"
            with tarfile.open(fileobj=io.BytesIO(body), mode=mode) as tf:
                keep_all = n in ("aapt2", "openjdk-17")
                files, elfs, prov = scan_tar(tf, lambda p: keep_all)
            for p in prov:
                provided.setdefault(p, n)
            if keep_all:
                binaries += [dict(e, pkg=n) for e in elfs]
            t["packages"][n] = {"files": files, "elf_count": len(elfs), "sha256": hashlib.sha256(raw).hexdigest(), "provides": sorted(set(prov))[:40]}
        except Exception:
            err("termux " + n)
    unresolved = {}
    for e in binaries:
        for need in e.get("needed", []):
            if need in SYS or need in provided:
                continue
            unresolved.setdefault(need, []).append(e["path"])
    t["unresolved_needed"] = {k: v[:3] for k, v in unresolved.items()}
    t["needed_by_package"] = sorted({(need, provided.get(need, "SYSTEM" if need in SYS else "?")) for e in binaries for need in e.get("needed", [])})
    t["binaries"] = [{"pkg": e["pkg"], "path": e["path"], "size": e["size"], "machine": e.get("machine"), "interp": e.get("interp"), "static": e.get("static"), "rpath": e.get("rpath"), "needed": e.get("needed")} for e in binaries if "/bin/" in e["path"] or e["path"].endswith("libjvm.so") or "aapt2" in e["path"]][:60]


def probe_lzhiyong():
    z = res["lzhiyong"]
    rels = json.loads(get("https://api.github.com/repos/lzhiyong/android-sdk-tools/releases?per_page=5"))
    z["releases"] = [{"tag": r["tag_name"], "assets": [{"name": a["name"], "size": a["size"], "url": a["browser_download_url"]} for a in r["assets"]]} for r in rels]
    done = 0
    for r in rels:
        for a in r["assets"]:
            nm = a["name"].lower()
            if "aarch64" not in nm or done >= 2 or a["size"] > 200 * 1024 * 1024:
                continue
            try:
                raw = get(a["browser_download_url"])
                found = []
                if nm.endswith(".zip"):
                    zf = zipfile.ZipFile(io.BytesIO(raw))
                    for n in zf.namelist():
                        if os.path.basename(n) == "aapt2":
                            e = elf(zf.read(n))
                            found.append({"path": n, "sha256": hashlib.sha256(zf.read(n)).hexdigest(), "elf": e})
                else:
                    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tf:
                        for m in tf:
                            if m.isfile() and os.path.basename(m.name) == "aapt2":
                                data = tf.extractfile(m).read()
                                found.append({"path": m.name, "sha256": hashlib.sha256(data).hexdigest(), "elf": elf(data)})
                z.setdefault("downloaded", []).append({"asset": a["name"], "tag": r["tag_name"], "sha256": hashlib.sha256(raw).hexdigest(), "aapt2": found})
                done += 1
            except Exception:
                err("lzhiyong " + a["name"])


for fn in (probe_lzhiyong, probe_termux):
    try:
        fn()
    except Exception:
        err(fn.__name__)

os.makedirs(OUT, exist_ok=True)
with open(os.path.join(OUT, "probe.json"), "w") as f:
    json.dump(res, f, indent=1)
print(json.dumps({"errors": res["errors"], "unresolved": res["termux"].get("unresolved_needed")}, indent=1)[:3000])
