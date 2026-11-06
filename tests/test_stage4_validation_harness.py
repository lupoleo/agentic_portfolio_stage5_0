"""Offline tests for the E2E-S4.0A validation harness (tools/stage4_validation)."""
from __future__ import annotations

import json
import os
import socket
import sqlite3
from pathlib import Path

import pytest

from app.ai.research_service import RESEARCH_CONTRACT_VERSION
from tools import stage4_validation as harness


def test_windows_absolute_detection():
    assert harness.is_windows_absolute(r"C:\Users\x\repo\data\a.json")
    assert harness.is_windows_absolute("d:/repo/a.json")
    assert harness.is_windows_absolute(r"\\server\share\a.json")
    assert not harness.is_windows_absolute(r"data\state\portfolio_cio.db")
    assert not harness.is_windows_absolute("data/state/portfolio_cio.db")


def test_locate_source_rebases_foreign_absolute_path_onto_repository(tmp_path):
    repo = tmp_path / "agentic_portfolio_stage4_0"
    persisted = (
        r"C:\Users\someone\OneDrive\Investimenti\agentic_portfolio_stage4_0"
        r"\data\cache\scanner\report.json"
    )
    located = harness.locate_source(persisted, repo)
    if os.name == "nt":
        assert located == Path(persisted)
    else:
        assert located == repo / "data" / "cache" / "scanner" / "report.json"


def test_locate_source_normalizes_relative_windows_separators(tmp_path):
    located = harness.locate_source(r"data\state\portfolio_cio.db", tmp_path)
    assert located == tmp_path / "data" / "state" / "portfolio_cio.db"


@pytest.mark.skipif(os.name == "nt", reason="foreign absolute paths are native on Windows")
def test_locate_source_refuses_unrelated_foreign_absolute_path(tmp_path):
    with pytest.raises(harness.HarnessError) as error:
        harness.locate_source(r"C:\elsewhere\report.json", tmp_path / "repo")
    assert error.value.code == "FOREIGN_ABSOLUTE_PATH"


def test_sandbox_target_keeps_persisted_string_verbatim(tmp_path):
    target = harness.sandbox_target(tmp_path, r"data\state\portfolio_cio.db")
    assert target == Path(os.path.join(str(tmp_path), r"data\state\portfolio_cio.db"))
    absolute = str(tmp_path / "x.json")
    assert harness.sandbox_target(tmp_path, absolute) is None


def test_block_network_refuses_and_restores():
    original = socket.socket.connect
    with harness.block_network() as attempts:
        with pytest.raises(ConnectionRefusedError):
            socket.create_connection(("127.0.0.1", 9))
        with socket.socket() as raw, pytest.raises(ConnectionRefusedError):
            raw.connect(("127.0.0.1", 9))
    assert len(attempts) == 2
    assert socket.socket.connect is original


def test_sqlite_copy_is_consistent_and_source_is_untouched(tmp_path):
    source = tmp_path / "source.db"
    with sqlite3.connect(source) as connection:
        connection.execute("create table ai_inferences (id integer)")
        connection.execute("insert into ai_inferences values (1), (2)")
    digest = harness.sha256_file(source)
    target = tmp_path / "copy" / "state.db"
    harness.sqlite_consistent_copy(source, target)
    counts = harness.table_counts(target)
    assert counts["ai_inferences"] == 2
    assert counts["trade_opportunities"] is None
    assert harness.sha256_file(source) == digest


def _report(**configuration):
    base = {
        "database_path": r"data\state\portfolio_cio.db",
        "eligibility_report_path": "eligibility.json",
        "mapping_report_path": "mapping.json",
        "history_report_paths": ["history.json"],
        "selected_parameters": None,
    }
    base.update(configuration)
    return {
        "configuration": base,
        "request": {"portfolio_file": "portfolio.xlsx", "portfolio_file_fingerprint": "x"},
        "run": {"run_id": "s4a-test", "watch_universe_report_path": None},
    }


def test_materialize_refuses_absolute_database_path(tmp_path):
    report = _report(database_path=r"C:\prod\portfolio_cio.db")
    with pytest.raises(harness.HarnessError) as error:
        harness.materialize_sandbox(report, tmp_path / "sandbox", tmp_path)
    assert error.value.code == "ABSOLUTE_DB_PATH_REFUSED"


def test_materialize_refuses_operator_overlay(tmp_path):
    report = _report(selected_parameters={"requested_exposure_eur": 1000})
    with pytest.raises(harness.HarnessError) as error:
        harness.materialize_sandbox(report, tmp_path / "sandbox", tmp_path)
    assert error.value.code == "SELECTED_PARAMETERS_NOT_SUPPORTED"


def test_materialize_copies_inputs_and_database(tmp_path):
    repo = tmp_path / "repo"
    (repo / "data" / "state").mkdir(parents=True)
    with sqlite3.connect(repo / "data" / "state" / "portfolio_cio.db") as connection:
        connection.execute("create table ai_inferences (id integer)")
    for name in ("eligibility.json", "mapping.json", "history.json"):
        (repo / name).write_text("{}", encoding="utf-8")
    (repo / "portfolio.xlsx").write_bytes(b"portfolio")
    report = _report()
    report["request"]["portfolio_file_fingerprint"] = harness.sha256_file(repo / "portfolio.xlsx")

    sandbox = harness.materialize_sandbox(report, tmp_path / "sandbox", repo)

    assert sandbox["portfolio_fingerprint_matches"] is True
    assert Path(sandbox["sandbox_database"]).is_file()
    assert {item["mode"] for item in sandbox["files"]} == {"COPIED"}


def test_main_reports_missing_root_report_as_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(harness, "REPO_ROOT", tmp_path)

    def missing(run_id, repo_root=tmp_path):
        raise harness.HarnessError("ROOT_REPORT_NOT_FOUND", "none")

    monkeypatch.setattr(harness, "find_root_report", missing)
    out = tmp_path / "replay.json"
    code = harness.main(["replay", "--workdir", str(tmp_path / "w"), "--out", str(out)])
    assert code == 1
    assert json.loads(out.read_text(encoding="utf-8"))["error"] == "ROOT_REPORT_NOT_FOUND"
    capsys.readouterr()


def _inspection_db(path, *, adapter_version, volatility_text, research_unknowns):
    with sqlite3.connect(path) as connection:
        for table, column in (
            ("scanner_evidence_bundles", "created_at"),
            ("opportunity_research", "created_at"),
            ("opportunity_scores", "created_at"),
            ("scanner_research_outcomes", "updated_at"),
        ):
            connection.execute(f"create table {table} ({column} text, payload_json text)")
        technical_text = "RSI14: 50.0000." + (
            " 20-session annualized volatility: 23.4000%." if volatility_text else ""
        )
        bundle = {
            "bundle_id": "evidence-1",
            "ticker": "UCG.MI",
            "items": [{
                "kind": "TECHNICAL",
                "evidence": {"text": technical_text},
                "metadata": {"adapter_version": adapter_version},
                "source": {"metadata": {"canonical_technical_contract": "ai-8c3-canonical-technical-v2"}},
            }],
        }
        research = {
            "research_id": "RES-1",
            "ticker": "UCG.MI",
            "research_status": "PARTIAL",
            "evidence_quality": "MEDIUM",
            "unknowns": research_unknowns,
        }
        outcome = {
            "hypothesis_id": "hyp-1",
            "status": "EXCLUDED",
            "reason": "RESEARCH_NOT_COMPLETE",
            "research_id": "RES-1",
            "evidence_bundle_id": "evidence-1",
        }
        stamp = "2026-09-30T12:30:00Z"
        connection.execute("insert into scanner_evidence_bundles values (?, ?)", (stamp, json.dumps(bundle)))
        connection.execute("insert into opportunity_research values (?, ?)", (stamp, json.dumps(research)))
        connection.execute("insert into scanner_research_outcomes values (?, ?)", (stamp, json.dumps(outcome)))


def test_inspection_accepts_r1_when_volatility_is_stated_and_not_unknown(tmp_path):
    database = tmp_path / "state.db"
    _inspection_db(
        database,
        adapter_version="stage4-research-technical-v2",
        volatility_text=True,
        research_unknowns=["Sector peer comparison is unavailable.", "Management tone."],
    )
    result = harness.inspect_research(database, "2026-09-30T12:00:00Z")
    assert result["r1_acceptance"] == "PASS"
    row = result["research"][0]
    assert row["bundle_states_volatility"] is True
    assert row["volatility_listed_unknown"] is False
    assert row["material_unknowns"] == ["Sector peer comparison is unavailable."]
    assert result["outcome_reason_counts"] == {"EXCLUDED/RESEARCH_NOT_COMPLETE": 1}


def test_inspection_rejects_r1_when_volatility_unknown_despite_evidence(tmp_path):
    database = tmp_path / "state.db"
    _inspection_db(
        database,
        adapter_version="stage4-research-technical-v2",
        volatility_text=True,
        research_unknowns=["Technical volatility metrics are unknown."],
    )
    result = harness.inspect_research(database, "2026-09-30T12:00:00Z")
    assert result["r1_acceptance"] == "FAIL"


def test_inspection_rejects_r1_for_v1_evidence_and_ignores_older_rows(tmp_path):
    database = tmp_path / "state.db"
    _inspection_db(
        database,
        adapter_version="stage4-research-technical-v1",
        volatility_text=False,
        research_unknowns=[],
    )
    assert harness.inspect_research(database, "2026-09-30T12:00:00Z")["r1_acceptance"] == "FAIL"
    later = harness.inspect_research(database, "2026-09-30T13:00:00Z")
    assert later["technical_evidence"] == [] and later["research"] == []


def _score_payload(kind, direction, raw, source="HYPOTHESIS"):
    return {
        "opportunity_score_id": f"score-{kind}",
        "ticker": "UCG.MI",
        "raw_score": raw,
        "technical_score": 50.0,
        "metadata": {
            "hypothesis_kind": kind,
            "scoring_diagnostics": {"direction": {
                "direction": direction, "direction_source": source,
                "policy_version": "ai-8c3-directional-scoring-v3",
            }},
        },
    }


def _insert_scores(database, payloads):
    with sqlite3.connect(database) as connection:
        for payload in payloads:
            connection.execute(
                "insert into opportunity_scores values (?, ?)",
                ("2026-09-30T12:30:00Z", json.dumps(payload)),
            )


def test_r2_inspection_accepts_directional_non_contradictory_pairs(tmp_path):
    database = tmp_path / "state.db"
    _inspection_db(database, adapter_version="stage4-research-technical-v2",
                   volatility_text=True, research_unknowns=[])
    _insert_scores(database, [
        _score_payload("NEW_LONG", "LONG", 64.0),
        _score_payload("NEW_SHORT", "SHORT", 38.0),
    ])
    result = harness.inspect_research(database, "2026-09-30T12:00:00Z")
    assert result["r2_acceptance"] == "PASS"
    assert result["direction_pairs"][0]["long_raw"] == 64.0


def test_r2_inspection_rejects_contradictory_or_undirected_scores(tmp_path):
    database = tmp_path / "state.db"
    _inspection_db(database, adapter_version="stage4-research-technical-v2",
                   volatility_text=True, research_unknowns=[])
    _insert_scores(database, [
        _score_payload("NEW_LONG", "LONG", 64.0),
        _score_payload("NEW_SHORT", "LONG", 62.0, source="DEFAULT_LONG"),
    ])
    result = harness.inspect_research(database, "2026-09-30T12:00:00Z")
    assert result["r2_acceptance"] == "FAIL"
    assert [check["passed"] for check in result["r2_checks"]] == [False, True, True, False]


def test_r2_inspection_is_not_applicable_without_directional_scores(tmp_path):
    database = tmp_path / "state.db"
    _inspection_db(database, adapter_version="stage4-research-technical-v2",
                   volatility_text=True, research_unknowns=[])
    assert harness.inspect_research(database, "2026-09-30T12:00:00Z")["r2_acceptance"] == "NOT_APPLICABLE"


def test_r1_inspection_treats_quoted_volatility_as_forward_uncertainty(tmp_path):
    database = tmp_path / "state.db"
    _inspection_db(
        database,
        adapter_version="stage4-research-technical-v2",
        volatility_text=True,
        research_unknowns=["Volatility persistence beyond current 22.37% level"],
    )
    result = harness.inspect_research(database, "2026-09-30T12:00:00Z")
    assert result["r1_acceptance"] == "PASS"
    row = result["research"][0]
    assert row["volatility_listed_unknown"] is False
    assert row["volatility_forward_mentions"] == ["Volatility persistence beyond current 22.37% level"]


def test_c2r1_inspection_reports_gaps_and_contract(tmp_path):
    database = tmp_path / "state.db"
    _inspection_db(database, adapter_version="stage4-research-technical-v2",
                   volatility_text=True, research_unknowns=[])
    with sqlite3.connect(database) as connection:
        connection.execute("delete from opportunity_research")
        research = {
            "research_id": "RES-2", "ticker": "HPE", "research_status": "COMPLETE",
            "evidence_quality": "HIGH", "unknowns": [],
            "forward_uncertainties": ["Long-term impact of new contracts on margins"],
            "research_confidence": 0.85,
            "metadata": {"research_contract": RESEARCH_CONTRACT_VERSION},
        }
        connection.execute(
            "insert into opportunity_research values (?, ?)",
            ("2026-09-30T12:30:00Z", json.dumps(research)),
        )
    result = harness.inspect_research(database, "2026-09-30T12:00:00Z")
    assert result["c2r1_acceptance"] == "PASS"
    assert result["complete_research_count"] == 1
    assert result["research"][0]["material_gaps"] == []


def test_inspection_summarizes_evidence_kinds_per_research(tmp_path):
    database = tmp_path / "state.db"
    _inspection_db(database, adapter_version="stage4-research-technical-v2",
                   volatility_text=True, research_unknowns=[])
    result = harness.inspect_research(database, "2026-09-30T12:00:00Z")
    assert result["research"][0]["evidence_kinds"] == {"TECHNICAL": 1}
    assert result["evidence_bundles"]["evidence-1"]["warnings"] == []


def test_inspection_reports_fundamental_period_age(tmp_path):
    database = tmp_path / "state.db"
    _inspection_db(database, adapter_version="stage4-research-technical-v2",
                   volatility_text=True, research_unknowns=[])
    with sqlite3.connect(database) as connection:
        bundle = {
            "bundle_id": "evidence-2", "ticker": "WBD", "created_at": "2026-09-30T19:08:10Z",
            "items": [{"kind": "FUNDAMENTAL", "evidence": {
                "text": "Fundamental company snapshot.", "published_at": "2026-06-30T00:00:00Z",
            }}],
        }
        connection.execute(
            "insert into scanner_evidence_bundles values (?, ?)",
            ("2026-09-30T19:08:10Z", json.dumps(bundle)),
        )
    result = harness.inspect_research(database, "2026-09-30T12:00:00Z")
    summary = result["evidence_bundles"]["evidence-2"]
    assert summary["fundamental_period_end"] == "2026-06-30T00:00:00Z"
    assert summary["fundamental_age_days"] == 92.8


def test_r2_2_inspection_flags_divergent_shared_company_frames(tmp_path):
    database = tmp_path / "state.db"
    _inspection_db(database, adapter_version="stage4-research-technical-v2",
                   volatility_text=True, research_unknowns=[])

    def payload(kind, direction, frame):
        value = _score_payload(kind, direction, 50.0)
        value["metadata"]["scoring_diagnostics"]["direction"].update({
            "company_frame_components": frame,
            "company_assessment": {
                "sources": {"fundamental": "SHARED_COMPANY_ASSESSMENT",
                            "expectations": "SHARED_COMPANY_ASSESSMENT"},
                "assessment": {"fingerprint": "abc"},
            },
        })
        return value

    _insert_scores(database, [
        payload("NEW_LONG", "LONG", {"fundamental": 60.0, "expectations": 55.0}),
        payload("NEW_SHORT", "SHORT", {"fundamental": 60.0, "expectations": 55.0}),
    ])
    result = harness.inspect_research(database, "2026-09-30T12:00:00Z")
    assert result["r2_acceptance"] == "PASS"
    assert result["direction_pairs"][0]["shared_assessment"] is True


def test_r1_inspection_ignores_volatility_implications(tmp_path):
    # Live 2026-09-30 (UCG.MI): an impact statement is not a missing metric.
    database = tmp_path / "state.db"
    _inspection_db(
        database,
        adapter_version="stage4-research-technical-v2",
        volatility_text=True,
        research_unknowns=["Impact of recent volatility on long-term valuation"],
    )
    result = harness.inspect_research(database, "2026-09-30T12:00:00Z")
    assert result["research"][0]["volatility_listed_unknown"] is False
    assert result["r1_acceptance"] == "PASS"


def test_r1_inspection_still_flags_missing_volatility_claims(tmp_path):
    database = tmp_path / "state.db"
    _inspection_db(
        database,
        adapter_version="stage4-research-technical-v2",
        volatility_text=True,
        research_unknowns=["Technical volatility metrics are unknown"],
    )
    assert harness.inspect_research(database, "2026-09-30T12:00:00Z")["r1_acceptance"] == "FAIL"


def test_inspection_reports_processing_failure_diagnostics(tmp_path):
    database = tmp_path / "state.db"
    _inspection_db(database, adapter_version="stage4-research-technical-v2",
                   volatility_text=True, research_unknowns=[])
    with sqlite3.connect(database) as connection:
        failed = {
            "hypothesis_id": "hyp-9", "kind": "NEW_SHORT", "subject_value": "FMC",
            "status": "FAILED", "reason": "PROCESSING_FAILED",
            "diagnostics": ["ValueError: fundamental is SCORABLE but targeted repair returned no assessment"],
        }
        connection.execute("insert into scanner_research_outcomes values (?, ?)",
                           ("2026-09-30T12:31:00Z", json.dumps(failed)))
    result = harness.inspect_research(database, "2026-09-30T12:00:00Z")
    assert result["processing_failures"] == [{
        "subject": "FMC", "kind": "NEW_SHORT", "hypothesis_id": "hyp-9",
        "diagnostics": ["ValueError: fundamental is SCORABLE but targeted repair returned no assessment"],
    }]
