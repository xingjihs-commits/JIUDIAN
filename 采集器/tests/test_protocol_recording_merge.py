# -*- coding: utf-8 -*-
import json
import tempfile
from pathlib import Path

from bridgecore.protocol_learner import (
    ProtocolLearner,
    ProtocolLearnResult,
    merge_learn_results,
    merge_recordings_into_result,
    _samples_from_read_records,
)


def test_samples_from_read_records_extracts_payload():
    records = [{
        "_type": "call_complete",
        "fn_name": "read_card",
        "ret": {"ok": True, "ret": 0, "out": {"payload": "C92B20B7" * 4}},
    }]
    samples = _samples_from_read_records(records, session_tag="read_written")
    assert len(samples) == 1
    assert samples[0]["type"] == "guest"
    assert samples[0]["hex"].startswith("C92B20B7")


def test_merge_learn_results_fills_gaps():
    base = ProtocolLearnResult()
    base.card_types = {"guest": {}}
    base.confidence = 0.5

    extra = ProtocolLearnResult()
    extra.card_types = {"master": {}}
    extra.checksum_algorithm = "crc16_modbus"
    extra.layout = {"lock_no_offset": 6}
    extra.confidence = 0.7

    merge_learn_results(base, extra)
    assert "master" in base.card_types
    assert base.checksum_algorithm == "crc16_modbus"
    assert base.layout["lock_no_offset"] == 6
    assert base.confidence >= 0.7


def test_merge_recordings_into_result_from_jsonl():
    learner = ProtocolLearner()
    base = ProtocolLearnResult()
    base.card_types = {"guest": {}}
    base.confidence = 0.6

    with tempfile.TemporaryDirectory() as tmp:
        rec_path = Path(tmp) / "rec_test_read_written.jsonl"
        header = {
            "_type": "session_header",
            "session_id": "abc123",
            "session_tag": "read_written",
            "record_count": 1,
        }
        call = {
            "_type": "call_complete",
            "fn_name": "read_card",
            "ret": {"ok": True, "ret": 0, "out": {"payload": "DEADBEEF" * 4}},
        }
        with open(rec_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(header, ensure_ascii=False) + "\n")
            f.write(json.dumps(call, ensure_ascii=False) + "\n")

        n = merge_recordings_into_result(learner, base, tmp)
        assert n == 1
        assert base.confidence >= 0.6
