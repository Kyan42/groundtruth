"""Properties gate: discovery, verdicts, the answer loop and the two-doors variants, in four parallel streams.

    .venv/bin/python gates/props_gate.py cbay | qtycap | lipsync | twodoors | unseen | ways | data | claimsclean | claimsbugs
    .venv/bin/python gates/props_gate.py --check-only RUN_DIR CHECKSET      # no apps, no service, no model
    .venv/bin/python gates/props_gate.py --check-only RUN_DIR ways-score KEY_ENTRY.json   # one copy's key entry

`unseen` runs p3/p4 (more.yml) on the original cbay as a clean reference, then p1-p4 (all.yml) on two variants it has
not seen; only after both variant runs completed does it open gates/work/unseen/KEY.json and compare verdicts.
It shares port 3100 and the seed reset with `cbay`: never run those two at the same time.
`ways` runs p1 (cart.yml with only p1) on the four sealed copies gates/work/ways/w1..w4 in turn; only after all four
runs returned, and only if all four completed, does it open gates/work/ways/KEY.json and score how each copy's ways in
were found (props_lib.score_ways).
`data` runs p1 (cart.yml with only p1) on three cbay copies with data tools (.groundtruth/data.yml, DATATOOLS.md):
gates/work/cbay-data-clean (p1 holds), cbay-data-bug (a listing of $1,000.00 or more enters the cart at the wrong
amount; the seed has none, so p1 must fail on cases that made one with make_listing) and cbay-data-bug-no-tool (no tool
can make a listing; a case must come back could-not-run with a request for one). See gates/variants/DATA-COPIES.md.
`claimsclean` and `claimsbugs` run the dashboard's claims (dashboard/.groundtruth/features/claims.yml: tabs, result,
film) on copies of the dashboard page built by gates/variants/build_dash_copies.py (see gates/variants/DASH-COPIES.md):
each copy is served by a results store started in this process (page folder = the copy, data = the copy's
.groundtruth/test-store), registered with no process id and the copy's reset_store.py, and verified through the
stream's own verifier service. `claimsclean` verifies all three claims on gates/work/dash-clean; `claimsbugs` verifies
one target claim on each broken copy: dash-regression-short (tabs), dash-single-holds (result), dash-other-test-film
(film). Both refuse to start if a copy is missing or its claims.yml is not byte-identical to the dashboard's.
The pass rule, decided before any paid run:
- Clean copy, all three claims: no variation broke on any claim. `tabs` and `result` must come out "holds". The verdict of `film` is reported but not judged, because its sentence says "with the pointer visible" and the verifier is never shown a film's picture, so that part cannot be checked.
- Each broken copy, its target claim only: at least one variation broke, on a case whose recorded starting data makes the bug able to show; and no variation broke on a case where the bug could not show.
- "Able to show" is read from each case's `result.starts_from` (the `make_run` steps that ran, without error), never from the plan.
- A question, "outside the wording" or "could not run" is never counted as a catch.
Each stream uses its own ports and its own verifier service (GT_SERVICE), prints one PASS/FAIL line per check,
always stops what it started, writes gates/results/props_<stream>.json and exits 0 only if every check passed.
Checksets for --check-only: see props_lib.CHECKSETS.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gates import apps, props_lib as L  # noqa: E402

PORTS = {"cbay": (8790, [3100]), "qtycap": (8792, [3103]), "lipsync": (8793, [8765, 8767]), "twodoors": (8794, [3104, 3105]),
         "unseen": (8795, [3100, 3106, 3107]), "ways": (8796, [3321, 3322, 3323, 3324]),
         "data": (8797, [3331, 3332, 3333]),
         "claimsclean": (8798, [8921]), "claimsbugs": (8799, [8922, 8923, 8924])}
TIMEOUT_FULL, TIMEOUT_ONE, TIMEOUT_PLAN, TIMEOUT_ALL = 3600, 1800, 1200, 5400
UNSEEN = apps.WORK / "unseen"          # variant-a, variant-b and the sealed KEY.json (read only by L.open_key)
VARIANTS = ("variant-a", "variant-b")
WAYS = apps.WORK / "ways"              # w1..w4 and the sealed KEY.json (read only by L.open_key, in _score_ways)
COPIES = ("w1", "w2", "w3", "w4")


class Stream:
    def __init__(self, name):
        self.name, self.chk, self.runs, self.procs = name, L.Checks(name), [], []
        self.key = None                    # the unseen/ways stream's answer file, once opened
        self.extra = {}                    # more top-level fields for the results file (the ways score table)

    def start(self, proc):
        self.procs.append(proc)
        return proc

    def stop_all(self):
        while self.procs:
            try:
                apps.stop(self.procs.pop())
            except Exception as e:  # noqa: BLE001
                print(f"[{self.name}] stop failed: {e}", flush=True)

    def step(self, label, fn, *a, **kw):
        """Run one step; an exception becomes a failed check and the stream continues. Returns fn's value or None."""
        try:
            return fn(*a, **kw)
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {str(e).strip().splitlines()[0] if str(e).strip() else ''}"
            self.chk.add(label, "-", "step_error", False, f"{getattr(fn, '__name__', fn)}: {msg}")
            traceback.print_exc()
            return None

    def verify_and_check(self, label, checkset, app_dir, feature, **kw):
        run_dir = self.step(label, L.verify, app_dir, feature, **kw)
        if run_dir is None:
            return None
        self.runs.append(run_dir)
        print(f"[{self.name}] {label}: checking {run_dir} with {checkset}", flush=True)
        L.CHECKSETS[checkset](self.chk, label, run_dir)
        return run_dir

    def write(self):
        out = {"passed": self.chk.passed, "checks": self.chk.items, "usage": L.usage(self.runs),
               "runs": [str(r) for r in self.runs]}
        if self.chk.notes:
            out["notes"] = self.chk.notes
        out.update(self.extra)
        if self.key is not None:
            out["key"] = self.key
        L.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        f = L.RESULTS_DIR / f"props_{self.name}.json"
        f.write_text(json.dumps(out, indent=2))
        print(f"[{self.name}] {'PASSED' if out['passed'] else 'FAILED'}: "
              f"{sum(c['ok'] for c in self.chk.items)}/{len(self.chk.items)} checks; usage {out['usage']}; wrote {f}", flush=True)
        return out["passed"]


# ---------------------------------------------------------------- streams
def stream_cbay(s: Stream):
    port = PORTS["cbay"][1][0]
    app_dir = apps.CBAY_APP
    s.start(L.start_next(app_dir, port))
    L.register_cbay(app_dir, port)

    # a. full run
    s.verify_and_check("full", "cbay-full", app_dir, "cart", timeout=TIMEOUT_FULL)

    # b. the answer loop, on a scratch feature
    s.step("decide", _answer_loop, s, app_dir)

    # c. two plan-only runs
    for i in (1, 2):
        s.verify_and_check(f"plan-only-{i}", "cbay-plan-only", app_dir, "cart", plan_only=True, timeout=TIMEOUT_PLAN)


def _answer_loop(s: Stream, app_dir: Path):
    """Scratch feature cart-decide (only p0): run, answer the first question intended, run again; always cleaned up."""
    gt = Path(app_dir) / ".groundtruth"
    scratch, dfile = gt / "features" / "cart-decide.yml", gt / "decisions" / "cart-decide.json"
    try:
        for f in (scratch, dfile):
            f.unlink(missing_ok=True)
        L.write_feature(app_dir, "cart-decide", "cart-decide", gt / "features" / "cart.yml", ["p0"])
        first = s.verify_and_check("decide-1", "cbay-decide-first", app_dir, "cart-decide", timeout=TIMEOUT_ONE)
        p0 = L.props_of(L.load_run(first)).get("p0", {}) if first else {}
        qs = [q for q in p0.get("questions") or [] if isinstance(q, dict) and q.get("case")]
        if not qs:
            s.chk.add("decide", "p0", "decided", False, "the first run asked no question; nothing to answer")
        else:
            case = qs[0]["case"]
            if s.step("decide", L.decide, app_dir, "cart-decide", "p0", case, "intended") is not None:
                import yaml
                ids = [p.get("id") for p in (yaml.safe_load(scratch.read_text()) or {}).get("properties") or []]
                s.chk.add("decide", "p0", "feature_gained_p0-1", "p0-1" in ids, f"answered {case} intended; ids now {ids}")
                s.chk.add("decide", "p0", "decisions_file_exists", dfile.is_file(), str(dfile))
                s.verify_and_check("decide-2", "cbay-decide-second", app_dir, "cart-decide", timeout=TIMEOUT_ONE)
    finally:
        for f in (scratch, dfile):
            f.unlink(missing_ok=True)


def _variant(s: Stream, label, app_dir: Path, port: int, checkset: str):
    L.write_feature(app_dir, "cart", "cart", apps.CBAY_APP / ".groundtruth" / "features" / "cart.yml", ["p1"])
    proc = s.start(L.start_next(app_dir, port))
    try:
        L.register_cbay(app_dir, port)
        s.verify_and_check(label, checkset, app_dir, "cart", only="p1", timeout=TIMEOUT_ONE)
    finally:
        apps.stop(proc)
        s.procs.remove(proc)


def stream_qtycap(s: Stream):
    _variant(s, "qty-cap", apps.WORK / "cbay-qty-cap", PORTS["qtycap"][1][0], "qtycap")


def stream_twodoors(s: Stream):
    p_ok, p_broken = PORTS["twodoors"][1]
    s.step("two-doors", _variant, s, "two-doors", apps.WORK / "cbay-two-doors", p_ok, "twodoors")
    s.step("two-doors-broken", _variant, s, "two-doors-broken", apps.WORK / "cbay-two-doors-broken", p_broken, "twodoors-broken")


def _lipsync_one(s: Stream, label, version, port, app_dir, checkset, root=None, only=None):
    proc = s.start(apps.start_lipsync(version, port, root=root))
    try:
        L.register(app_dir, f"http://127.0.0.1:{port}")
        s.verify_and_check(label, checkset, app_dir, "lip-sync", only=only, timeout=TIMEOUT_ONE)
    finally:
        apps.stop(proc)
        s.procs.remove(proc)


def stream_lipsync(s: Stream):
    app_port, control_port = PORTS["lipsync"][1]
    s.step("agent", _lipsync_one, s, "agent", "agent", app_port, apps.DEMO, "lipsync-agent")
    s.step("fixed", _lipsync_one, s, "fixed", "fixed", app_port, apps.DEMO, "lipsync-fixed")
    control = s.step("control", apps.make_control_copy, apps.WORK / "control")
    if control is not None:
        s.step("control", _lipsync_one, s, "control", "fixed", control_port, control, "lipsync-control", root=control, only="g3")


MORE_GOAL = "Shoppers watch items and buy them."
ALL_GOAL = "Shoppers collect, watch and buy items."


def _cart_props(ids):
    import yaml
    src = yaml.safe_load((apps.CBAY_APP / ".groundtruth" / "features" / "cart.yml").read_text()) or {}
    props = [{"id": p["id"], "statement": p["statement"]} for p in src.get("properties") or [] if p.get("id") in ids]
    if [p["id"] for p in props] != ids:
        raise RuntimeError(f"the original cart.yml lacks some of {ids}")
    return props


def _write_props(app_dir: Path, name: str, goal: str, props: list[dict]) -> Path:
    f = Path(app_dir) / ".groundtruth" / "features" / f"{name}.yml"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(L.feature_text(name, goal, props))
    return f


def _unseen_variant(s: Stream, name: str, port: int) -> Path | None:
    app_dir = UNSEEN / name
    _write_props(app_dir, "all", ALL_GOAL, _cart_props(["p1", "p2"]) + L.MORE_PROPS)
    proc = s.start(L.start_next(app_dir, port))
    try:
        L.register_cbay(app_dir, port)
        return s.verify_and_check(name, "unseen-variant", app_dir, "all", timeout=TIMEOUT_ALL)
    finally:
        apps.stop(proc)
        s.procs.remove(proc)


def _compare_with_key(s: Stream, done: dict):
    """Runs only after both variant runs have finished. Opens the sealed key only if both completed."""
    status = {v: L.variant_complete(done.get(v)) for v in VARIANTS}
    incomplete = {v: why for v, (ok, why) in status.items() if not ok}
    if incomplete:
        for v in VARIANTS:
            for pid in L.UNSEEN_IDS:
                s.chk.add("key", f"{v}/{pid}", "matches_key", False,
                          f"key left sealed: {'; '.join(f'{k}: {w}' for k, w in incomplete.items())}")
        return
    try:
        key = L.open_key(UNSEEN / "KEY.json")
    except Exception as e:  # noqa: BLE001
        for v in VARIANTS:
            for pid in L.UNSEEN_IDS:
                s.chk.add("key", f"{v}/{pid}", "matches_key", False, f"could not read KEY.json: {type(e).__name__}: {e}")
        return
    s.key = key
    for v in VARIANTS:
        ps = L.props_of(L.load_run(done[v]))
        entry = key.get(v) if isinstance(key, dict) else None
        expected = entry.get("expected") if isinstance(entry, dict) and isinstance(entry.get("expected"), dict) else {}
        for pid in L.UNSEEN_IDS:
            want, got = expected.get(pid), (ps.get(pid) or {}).get("verdict")
            s.chk.add("key", f"{v}/{pid}", "matches_key", want is not None and want == got,
                      f"expected {want}, got {got}")
    for v in VARIANTS:
        entry = key.get(v) if isinstance(key, dict) and isinstance(key.get(v), dict) else {}
        print(f"[{s.name}] {v} planted: {entry.get('what')}", flush=True)
        print(f"[{s.name}] {v} how to see it: {entry.get('how_to_see_it')}", flush=True)


def stream_unseen(s: Stream):
    ref_port, *variant_ports = PORTS["unseen"][1]
    # The original cbay on 3100 is shared with the cbay stream (same app, env.json, seed reset): never run both at once.
    if apps._listening_pids(ref_port):
        s.chk.add("reference", "-", "port_free", False,
                  f"port {ref_port} is already in use (is the cbay stream running?); not taking it over")
    else:
        s.step("reference", _unseen_reference, s, ref_port)
    done = {}
    for name, port in zip(VARIANTS, variant_ports):
        done[name] = s.step(name, _unseen_variant, s, name, port)
    s.step("key", _compare_with_key, s, done)


def _unseen_reference(s: Stream, port: int):
    app_dir = apps.CBAY_APP
    _write_props(app_dir, "more", MORE_GOAL, L.MORE_PROPS)
    proc = s.start(L.start_next(app_dir, port))
    try:
        L.register_cbay(app_dir, port)
        s.verify_and_check("reference", "unseen-ref", app_dir, "more", timeout=TIMEOUT_ONE)
    finally:
        apps.stop(proc)
        s.procs.remove(proc)


def _ways_copy(s: Stream, name: str, port: int) -> Path | None:
    app_dir = WAYS / name
    L.write_feature(app_dir, "cart", "cart", apps.CBAY_APP / ".groundtruth" / "features" / "cart.yml", L.WAYS_IDS)
    proc = s.start(L.start_next(app_dir, port))
    try:
        L.register_cbay(app_dir, port)
        return s.verify_and_check(name, "ways-run", app_dir, "cart", timeout=TIMEOUT_ONE)
    finally:
        apps.stop(proc)
        s.procs.remove(proc)


def _score_ways(s: Stream, done: dict):
    """Runs only after all four copy runs have returned. Opens the sealed key only if all four completed."""
    status = {c: L.variant_complete(done.get(c), L.WAYS_IDS) for c in COPIES}
    incomplete = {c: why for c, (ok, why) in status.items() if not ok}
    if incomplete:
        why = "key left sealed: " + "; ".join(f"{k}: {w}" for k, w in incomplete.items())
        for c in COPIES:
            for name in L.WAYS_SCORE_CHECKS:
                s.chk.add(f"key-{c}", "p1", name, False, why)
        s.extra["scores"] = {"sealed": why}
        return
    try:
        key = L.open_key(WAYS / "KEY.json")
        if not isinstance(key, dict):
            raise ValueError(f"KEY.json holds a {type(key).__name__}, not an object")
    except Exception as e:  # noqa: BLE001
        for c in COPIES:
            for name in L.WAYS_SCORE_CHECKS:
                s.chk.add(f"key-{c}", "p1", name, False, f"could not read KEY.json: {type(e).__name__}: {e}")
        return
    s.key = key
    scores = {c: L.score_ways(s.chk, f"key-{c}", done[c], key.get(c)) for c in COPIES}
    totals = {"key_ways_in": sum(r["key_ways_in"] for r in scores.values()),
              "missed": sum(r["missed"] for r in scores.values()), "invented": sum(r["invented"] for r in scores.values())}
    s.extra["scores"], s.extra["totals"] = scores, totals
    for c, r in scores.items():
        print(f"[{s.name}] {c} ({r['kind']}): key ways in {r['key_ways_in']}, found {r['found']}, missed {r['missed']}, "
              f"invented {r['invented']}, to check {r['to_check']}, flagged {r['flagged']}; verdict {r['verdict']} "
              f"(key {r['expected_verdict']})", flush=True)
        print(f"[{s.name}] {c} what: {r['what']}", flush=True)
        print(f"[{s.name}] {c} how to see it: {r['how_to_see_it']}", flush=True)
    print(f"[{s.name}] totals: ways in in the key {totals['key_ways_in']}, missed {totals['missed']}, "
          f"invented {totals['invented']}", flush=True)


def stream_ways(s: Stream):
    done = {}
    for name, port in zip(COPIES, PORTS["ways"][1]):
        done[name] = s.step(name, _ways_copy, s, name, port)
    s.step("key", _score_ways, s, done)


DATA_COPIES = (("data-clean", "cbay-data-clean"), ("data-bug", "cbay-data-bug"), ("data-no-tool", "cbay-data-bug-no-tool"))


def stream_data(s: Stream):
    """Each copy registered with the shop's reset command (data tools refuse to run without one), then p1 verified.
    The label is also the checkset."""
    for (label, copy), port in zip(DATA_COPIES, PORTS["data"][1]):
        s.step(label, _variant, s, label, apps.WORK / copy, port, label)


# stream -> ((copy under gates/work, checkset, the one claim to verify or None for all), ...)
CLAIM_COPIES = {"claimsclean": (("dash-clean", "claims-clean", None),),
                "claimsbugs": tuple((copy, cs, target) for cs, (copy, target, _able) in L.CLAIM_BUGS.items())}


def claim_copy_problems(stream: str) -> list[str]:
    """Why a claims stream must not start: a copy missing, or its claims.yml not byte-identical to the dashboard's."""
    ref = apps.REPO / "dashboard" / L.CLAIMS_FILE
    want = ref.read_bytes() if ref.is_file() else None
    out = [] if want is not None else [f"{ref} does not exist"]
    for copy, _cs, _only in CLAIM_COPIES[stream]:
        d, f = apps.WORK / copy, apps.WORK / copy / L.CLAIMS_FILE
        if not d.is_dir():
            out.append(f"the copy {d} is missing")
        elif not f.is_file():
            out.append(f"{f} is missing")
        elif want is not None and f.read_bytes() != want:
            out.append(f"{f} differs from {ref}")
    return out


def _claims_copy(s: Stream, label: str, copy: str, port: int, checkset: str, only: str | None):
    app_dir = apps.WORK / copy
    os.environ.pop("GT_TEST_STORE_DIR", None)        # the copy's tools must use the copy's own test store
    store = s.start(L.InProcessStore(app_dir, port))
    try:
        L.register(app_dir, store.url, reset=L.STORE_RESET, no_pid=True)
        s.verify_and_check(label, checkset, app_dir, "claims", only=only, timeout=TIMEOUT_ALL if only is None else TIMEOUT_ONE)
    finally:
        apps.stop(store)
        s.procs.remove(store)
        try:
            print(f"[{s.name}] {label}: {L.reset_test_store(app_dir)}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[{s.name}] {label}: could not reset the test store: {e}", flush=True)


def stream_claims(s: Stream):
    """Each copy in turn on its own store port; the checkset's name is the label."""
    for (copy, checkset, only), port in zip(CLAIM_COPIES[s.name], PORTS[s.name][1]):
        s.step(checkset, _claims_copy, s, checkset, copy, port, checkset, only)


STREAMS = {"cbay": stream_cbay, "qtycap": stream_qtycap, "lipsync": stream_lipsync, "twodoors": stream_twodoors,
           "unseen": stream_unseen, "ways": stream_ways,
           "data": stream_data, "claimsclean": stream_claims, "claimsbugs": stream_claims}


def run_stream(name: str) -> bool:
    s = Stream(name)
    service_port = PORTS[name][0]
    os.environ["GT_SERVICE"] = f"http://127.0.0.1:{service_port}"
    print(f"[{name}] service {os.environ['GT_SERVICE']}, app ports {PORTS[name][1]}", flush=True)
    try:
        s.start(apps.start_service(service_port))
        s.step("stream", STREAMS[name], s)
    except Exception as e:  # noqa: BLE001  (service start failed)
        s.chk.add("stream", "-", "step_error", False, f"{type(e).__name__}: {e}")
        traceback.print_exc()
    finally:
        s.stop_all()
        passed = s.write()
    return passed


def check_only(run_dir: str, checkset: str, key_entry: str | None = None) -> bool:
    chk = L.Checks("check-only")
    if checkset in L.KEYED_CHECKSETS:
        if not key_entry:
            sys.exit(f"checkset {checkset} needs a key entry file: --check-only RUN_DIR {checkset} KEY_ENTRY.json")
        L.KEYED_CHECKSETS[checkset](chk, checkset, Path(run_dir).resolve(), json.loads(Path(key_entry).read_text()))
    elif checkset in L.CHECKSETS and not key_entry:
        L.CHECKSETS[checkset](chk, checkset, Path(run_dir).resolve())
    else:
        sys.exit(f"unknown checkset {checkset} (or a key file given to a checkset that takes none); choose from "
                 f"{', '.join(L.CHECKSETS)}; with a key entry file: {', '.join(L.KEYED_CHECKSETS)}")
    print(f"{'PASSED' if chk.passed else 'FAILED'}: {sum(c['ok'] for c in chk.items)}/{len(chk.items)} checks; "
          f"usage {L.usage([Path(run_dir)])}", flush=True)
    return chk.passed


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--check-only"] and len(argv) in (3, 4):
        sys.exit(0 if check_only(*argv[1:]) else 1)
    if len(argv) == 1 and argv[0] in STREAMS:
        if argv[0] in CLAIM_COPIES:
            problems = claim_copy_problems(argv[0])
            if problems:
                sys.exit(f"Not starting {argv[0]}: " + "; ".join(problems) + ". Rebuild the copies with "
                         ".venv/bin/python gates/variants/build_dash_copies.py")
        sys.exit(0 if run_stream(argv[0]) else 1)
    sys.exit(__doc__ + f"\nstreams: {', '.join(STREAMS)}\nchecksets: {', '.join(L.CHECKSETS)}"
             f"\nchecksets with a key entry file: {', '.join(L.KEYED_CHECKSETS)}")


if __name__ == "__main__":
    main()
