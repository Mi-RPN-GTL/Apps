#!/usr/bin/env python3
"""Builds the jdk pack from Termux (Android, aarch64) .deb files.

Layout of the pack:
  jdk/        JAVA_HOME (bin/java, lib/server/libjvm.so, lib/modules, ...)
  libs/       shared libraries from the dependency closure that Android does not have
Every ELF file is checked (aarch64, Android linker, all libraries resolvable) and
the build fails with a report instead of producing a pack that cannot run.
"""
import hashlib, io, json, os, posixpath, shutil, sys, tarfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import elfcheck

TERMUX = "https://packages.termux.dev/apt/termux-main/"
PREFIX = "data/data/com.termux/files/usr/"
PRUNE = ["jmods", "man", "include", "demo", "sample", "legal", "docs", "src.zip", "lib/src.zip"]
KEEP_BIN = {"java"}


def parse_index(txt):
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


def closure(pk, roots, skip=()):
    seen, todo = [], list(roots)
    while todo:
        n = todo.pop(0)
        if n in seen or n in skip or n not in pk:
            continue
        seen.append(n)
        for part in pk[n].get("Depends", "").split(","):
            nm = part.split("|")[0].split("(")[0].strip()
            if nm:
                todo.append(nm)
    return seen


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


def extract_deb(deb, stage, links):
    body, nm = ar_data(deb)
    mode = "r:xz" if nm.endswith("xz") else "r:gz" if nm.endswith("gz") else "r:*"
    n = 0
    with tarfile.open(fileobj=io.BytesIO(body), mode=mode) as tf:
        for m in tf:
            name = m.name.lstrip("./")
            if not name.startswith(PREFIX):
                continue
            rel = name[len(PREFIX):].rstrip("/")
            if not rel:
                continue
            if ".." in rel.split("/"):
                raise RuntimeError("unsafe path in deb: " + m.name)
            dest = os.path.join(stage, rel)
            if m.isdir():
                os.makedirs(dest, exist_ok=True)
            elif m.issym():
                links[rel] = m.linkname
            elif m.isfile():
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as o:
                    o.write(tf.extractfile(m).read())
                os.chmod(dest, 0o755 if (m.mode & 0o111) else 0o644)
                n += 1
    return n


def resolve_links(stage, links):
    """Turn symlinks into real copies (the pack format only holds regular files)."""
    dangling = []

    def target_of(rel, tgt):
        if tgt.startswith("/"):
            if tgt.startswith("/" + PREFIX):
                return tgt[len("/" + PREFIX):]
            return None
        return posixpath.normpath(posixpath.join(posixpath.dirname(rel), tgt))

    for _ in range(4):
        pending = {}
        for rel, tgt in links.items():
            t = target_of(rel, tgt)
            src = os.path.join(stage, t) if t is not None else None
            dest = os.path.join(stage, rel)
            if src is None or t.startswith(".."):
                dangling.append(rel)
                continue
            if os.path.isfile(src):
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copy2(src, dest)
            elif os.path.isdir(src):
                if not os.path.exists(dest):
                    shutil.copytree(src, dest, symlinks=False)
            else:
                pending[rel] = tgt
        if not pending or pending == links:
            dangling += list(pending)
            break
        links = pending
    return sorted(set(dangling))


def find_home(stage):
    hits = []
    for root, dirs, files in os.walk(stage):
        if root.endswith("/bin") and "java" in files:
            home = os.path.dirname(root)
            if os.path.exists(os.path.join(home, "lib", "server", "libjvm.so")):
                hits.append(home)
    if not hits:
        raise RuntimeError("no JAVA_HOME (bin/java plus lib/server/libjvm.so) found in the extracted packages")
    return sorted(hits, key=len)[0]


def all_elfs(top):
    for root, dirs, files in os.walk(top):
        for n in sorted(files):
            p = os.path.join(root, n)
            with open(p, "rb") as f:
                if f.read(4) == b"\x7fELF":
                    yield p


def build(work, fetch, lock, roots=("openjdk-17",), index_url=None, report=None):
    """work: pack dir to fill. fetch(url) -> bytes."""
    report = report if report is not None else {}
    idx = fetch(index_url or TERMUX + "dists/stable/main/binary-aarch64/Packages").decode(errors="replace")
    pk = parse_index(idx)
    for r in roots:
        if r not in pk:
            raise RuntimeError("Termux has no package " + r)
    names = closure(pk, roots)
    report["packages"] = names
    stage = os.path.join(work, ".stage")
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    links = {}
    for n in names:
        d = pk[n]
        raw = fetch(TERMUX + d["Filename"])
        h = hashlib.sha256(raw).hexdigest()
        if d.get("SHA256") and h != d["SHA256"]:
            raise RuntimeError("sha256 mismatch for " + n + ": index " + d["SHA256"] + " got " + h)
        lock["termux:" + n] = {"version": d.get("Version"), "file": d["Filename"], "sha256": h}
        extract_deb(raw, stage, links)
    report["dangling_links"] = resolve_links(stage, links)
    home = find_home(stage)
    jdk = os.path.join(work, "jdk")
    libs = os.path.join(work, "libs")
    shutil.rmtree(jdk, ignore_errors=True)
    shutil.rmtree(libs, ignore_errors=True)
    shutil.copytree(home, jdk)
    for p in PRUNE:
        t = os.path.join(jdk, p)
        shutil.rmtree(t, ignore_errors=True) if os.path.isdir(t) else (os.remove(t) if os.path.exists(t) else None)
    bindir = os.path.join(jdk, "bin")
    for n in os.listdir(bindir):
        if n not in KEEP_BIN:
            os.remove(os.path.join(bindir, n))
    bundle_libs(stage, jdk, libs, report)
    shutil.rmtree(stage, ignore_errors=True)
    check_pack(work, report)
    return report


def bundle_libs(stage, jdk, libs, report):
    os.makedirs(libs, exist_ok=True)
    have = set()
    for root, dirs, files in os.walk(jdk):
        have.update(files)
    stage_lib = os.path.join(stage, "lib")
    queue = list(all_elfs(jdk))
    bundled, unresolved = [], {}
    while queue:
        p = queue.pop()
        with open(p, "rb") as fh:
            e = elfcheck.elf(fh.read())
        if not e:
            continue
        for need in e.get("needed", []):
            if need in elfcheck.SYS or need in have:
                continue
            src = os.path.join(stage_lib, need)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(libs, need))
                have.add(need)
                bundled.append(need)
                queue.append(os.path.join(libs, need))
            else:
                unresolved.setdefault(need, []).append(os.path.relpath(p, os.path.dirname(libs)))
    report["bundled_libs"] = sorted(bundled)
    if unresolved:
        raise RuntimeError("libraries needed but not found in any package: " + json.dumps({k: v[:2] for k, v in unresolved.items()}))


def check_pack(work, report):
    jdk = os.path.join(work, "jdk")
    libs = os.path.join(work, "libs")
    provided = set(elfcheck.SYS)
    for top in (jdk, libs):
        for root, dirs, files in os.walk(top):
            provided.update(files)
    bad, count = [], 0
    for top in (jdk, libs):
        for p in all_elfs(top):
            count += 1
            try:
                elfcheck.validate(p, provided)
            except RuntimeError as ex:
                bad.append(str(ex)[:300])
    java = os.path.join(jdk, "bin", "java")
    if not os.path.exists(java):
        bad.append("jdk/bin/java is missing")
    if not os.path.exists(os.path.join(jdk, "lib", "server", "libjvm.so")):
        bad.append("jdk/lib/server/libjvm.so is missing")
    if not os.path.exists(os.path.join(jdk, "lib", "modules")):
        bad.append("jdk/lib/modules is missing")
    os.chmod(java, 0o755)
    report["elf_checked"] = count
    if bad:
        raise RuntimeError(str(len(bad)) + " problems in the jdk pack: " + " | ".join(bad[:5]))
