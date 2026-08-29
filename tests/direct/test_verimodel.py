import json
import pytest


def test_benchmark_success_and_bounty_release(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """
    Test complete successful lifecycle of VeriModel:
    1. Sponsor (Alice/DAO) creates an AI Benchmark Grant Challenge for Open-Weights Coding LLM with 100 GEN bounty and 30 GEN required developer stake.
    2. Developer (Bob) stakes 30 GEN collateral, activating the challenge.
    3. Live telemetry from committed HuggingFace Leaderboard API endpoint proves 84.6% HumanEval pass@1 (exceeding 80% target).
    4. GenLayer validators reach consensus on RELEASE_BOUNTY (conf: 96, benchmark_achieved: True).
    5. Full 130 GEN (100 GEN bounty + 30 GEN stake refund) is released to Bob.
    """
    contract = direct_deploy("contracts/verimodel.py")

    # Step 1: Alice creates challenge with 100 GEN bounty
    direct_vm.sender = direct_alice
    direct_vm.value = 100 * 10**18
    committed_url = "https://huggingface.co/api/models/open-llm-leaderboard/evals/deep-coder-v2"

    chal_id = contract.create_challenge(
        str(direct_bob),
        "CODING_HUMANEVAL",
        "Achieve HumanEval pass@1 >= 80.0% and MBPP >= 75.0% on public open-weights evaluation harness.",
        committed_url,
        30 * 10**18,  # Required stake: 30 GEN
        604800,  # 7 days
        "deep-coder-v2",
    )
    assert chal_id == 0

    c_init = contract.get_challenge(chal_id)
    assert c_init["sponsor"].lower() == str(direct_alice).lower()
    assert c_init["model_developer"].lower() == str(direct_bob).lower()
    assert c_init["model_identifier"] == "deep-coder-v2"
    assert c_init["evaluator_authority"] == "huggingface.co"
    assert c_init["status"] == "PENDING_STAKE"
    assert c_init["bounty_escrow"] == str(100 * 10**18)
    assert c_init["required_developer_stake"] == str(30 * 10**18)

    # Step 2: Bob stakes 30 GEN collateral to activate challenge
    direct_vm.sender = direct_bob
    direct_vm.value = 30 * 10**18
    contract.stake_and_enter_challenge(chal_id)

    c_active = contract.get_challenge(chal_id)
    assert c_active["status"] == "ACTIVE"
    assert c_active["developer_stake_deposited"] == str(30 * 10**18)

    # Step 3: Mock live authority leaderboard telemetry and multi-validator neural consensus
    direct_vm.mock_web(
        r".*huggingface\.co/api/.*",
        {
            "status": 200,
            "body": json.dumps({
                "model_id": "deep-coder-v2",
                "humaneval_pass1": 84.6,
                "mbpp_pass1": 78.2,
                "eval_harness_version": "v0.4.2",
                "reproducibility_verified": True,
            }),
        },
    )

    direct_vm.mock_llm(
        r".*VeriModel Decentralized AI Benchmark & Evaluation Adjudicator.*",
        json.dumps({
            "action_decision": "RELEASE_BOUNTY",
            "confidence_score": 96,
            "benchmark_achieved": True,
            "summary": "DeepCoder-V2 achieved 84.6% HumanEval (surpassing 80.0% threshold) and 78.2% MBPP. Full open-weights reproducibility verified on HuggingFace Leaderboard.",
        }),
    )

    # Step 4: Adjudicate benchmark bound to committed source
    direct_vm.sender = direct_bob
    contract.adjudicate_benchmark(
        chal_id,
        "Official benchmark run completed and verified on HF Leaderboard.",
        committed_url,
    )

    # Step 5: Verify challenge is RELEASED and finalized
    c_final = contract.get_challenge(chal_id)
    assert c_final["status"] == "RELEASED"
    assert c_final["adjudication_verdict"] == "RELEASE_BOUNTY"
    assert c_final["adjudication_confidence"] == 96
    assert c_final["is_finalized"] is True


def test_repeated_404_responses_leave_challenge_active_and_move_no_funds(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """
    Steward Verification Test:
    Verifies that HTTP 404 responses and repeated fetch/status failures are strictly treated
    as retry outcomes (EXTEND_EVAL_WINDOW), NEVER as evidence for slashing, leaving the challenge
    fully active and moving zero escrow funds.
    """
    contract = direct_deploy("contracts/verimodel.py")

    # Step 1: Alice creates challenge with 50 GEN bounty
    direct_vm.sender = direct_alice
    direct_vm.value = 50 * 10**18
    committed_url = "https://huggingface.co/api/models/open-llm-leaderboard/evals/pending-model-7b"

    chal_id = contract.create_challenge(
        str(direct_bob),
        "REASONING_MATH_MMLU",
        "Achieve MMLU >= 75.0%",
        committed_url,
        20 * 10**18,  # Required stake: 20 GEN
        604800,
        "pending-model-7b",
    )

    # Step 2: Bob stakes 20 GEN collateral
    direct_vm.sender = direct_bob
    direct_vm.value = 20 * 10**18
    contract.stake_and_enter_challenge(chal_id)

    stats_before = contract.get_protocol_stats()
    expected_liabilities = str(70 * 10**18)
    assert stats_before["total_active_liabilities"] == expected_liabilities

    # Step 3: First Adjudication Attempt - Authority endpoint returns HTTP 404 (endpoint not yet published)
    direct_vm.mock_web(
        r".*",
        {"status": 404, "body": "404 Not Found - Benchmark eval job is still compiling"},
    )

    direct_vm.sender = direct_alice
    contract.adjudicate_benchmark(chal_id, "Checking if benchmark published", committed_url)

    c_attempt1 = contract.get_challenge(chal_id)
    assert c_attempt1["status"] == "ACTIVE", "Challenge must remain ACTIVE on 404"
    assert c_attempt1["is_finalized"] is False, "Challenge must NOT reach finality on 404"
    assert c_attempt1["adjudication_verdict"] == "EXTEND_EVAL_WINDOW"
    assert c_attempt1["adjudication_confidence"] == 0
    assert "[EXTERNAL]" in c_attempt1["adjudication_summary"]
    assert "404" in c_attempt1["adjudication_summary"]

    stats_after_1 = contract.get_protocol_stats()
    assert stats_after_1["total_active_liabilities"] == expected_liabilities, "No funds must move on 404"

    # Step 4: Second Adjudication Attempt - Repeated HTTP 404 (still pending)
    direct_vm.sender = direct_bob
    contract.adjudicate_benchmark(chal_id, "Second check by developer", committed_url)

    c_attempt2 = contract.get_challenge(chal_id)
    assert c_attempt2["status"] == "ACTIVE", "Challenge must remain ACTIVE on repeated 404"
    assert c_attempt2["is_finalized"] is False, "Challenge must NOT reach finality on repeated 404"
    assert c_attempt2["adjudication_verdict"] == "EXTEND_EVAL_WINDOW"
    assert c_attempt2["adjudication_confidence"] == 0

    stats_after_2 = contract.get_protocol_stats()
    assert stats_after_2["total_active_liabilities"] == expected_liabilities, "Liabilities must remain 100% preserved"

    # Step 5: Third Adjudication Attempt - HTTP 503 Service Unavailable / Fetch error
    direct_vm.mock_web(
        r".*",
        {"status": 503, "body": "503 Service Unavailable"},
    )
    direct_vm.sender = direct_alice
    contract.adjudicate_benchmark(chal_id, "Third check during server maintenance", committed_url)

    c_attempt3 = contract.get_challenge(chal_id)
    assert c_attempt3["status"] == "ACTIVE", "Challenge must remain ACTIVE on 503 status failure"
    assert c_attempt3["is_finalized"] is False
    assert c_attempt3["adjudication_verdict"] == "EXTEND_EVAL_WINDOW"

    stats_after_3 = contract.get_protocol_stats()
    assert stats_after_3["total_active_liabilities"] == expected_liabilities, "Zero funds moved across all 3 failed fetch retries"


def test_sub_threshold_confidence_cannot_slash_and_moves_no_funds(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """
    Test that sub-threshold confidence (conf < 80) strictly prevents slashing
    and leaves the challenge active for subsequent retry without moving any funds.
    """
    contract = direct_deploy("contracts/verimodel.py")

    direct_vm.sender = direct_alice
    direct_vm.value = 40 * 10**18
    committed_url = "https://huggingface.co/api/models/open-llm-leaderboard/evals/ambiguous-model"

    chal_id = contract.create_challenge(
        str(direct_bob),
        "CODING_HUMANEVAL",
        "Achieve HumanEval >= 80.0%",
        committed_url,
        10 * 10**18,
        604800,
        "ambiguous-model",
    )

    direct_vm.sender = direct_bob
    direct_vm.value = 10 * 10**18
    contract.stake_and_enter_challenge(chal_id)

    # Mock ambiguous telemetry where LLM confidence is only 65 (below 80 threshold)
    direct_vm.mock_web(r".*", {"status": 200, "body": json.dumps({"telemetry": "incomplete_log", "preliminary_score": 62.0})})
    direct_vm.mock_llm(
        r".*",
        json.dumps({
            "action_decision": "SLASH_CHALLENGE",
            "confidence_score": 65,  # SUB-THRESHOLD CONFIDENCE (< 80)
            "benchmark_achieved": False,
            "summary": "Preliminary logs indicate low score, but telemetry is noisy and uncertain (confidence 65%).",
        }),
    )

    direct_vm.sender = direct_alice
    contract.adjudicate_benchmark(chal_id, "Premature slash attempt with weak evidence", committed_url)

    c = contract.get_challenge(chal_id)
    assert c["status"] == "ACTIVE", "Sub-threshold confidence must NOT slash the challenge"
    assert c["is_finalized"] is False, "Must remain active for retry"
    assert c["adjudication_verdict"] == "EXTEND_EVAL_WINDOW"
    assert c["adjudication_confidence"] == 65
    assert "[EXPECTED] Sub-threshold confidence" in c["adjudication_summary"]

    stats = contract.get_protocol_stats()
    assert stats["total_active_liabilities"] == str(50 * 10**18), "Zero funds moved"


def test_benchmark_falsification_slashing_with_high_confidence(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """
    Test affirmative benchmark falsification & developer stake slashing with high confidence:
    1. Telemetry proves 52.4% MMLU against 80.0% required with verified contamination.
    2. Validators verify failure on live telemetry and reach consensus on SLASH_CHALLENGE (conf: 98 >= 80).
    3. Full 130 GEN (100 GEN bounty refund + 30 GEN slashed developer stake) is awarded to the Sponsor (Alice).
    """
    contract = direct_deploy("contracts/verimodel.py")

    direct_vm.sender = direct_alice
    direct_vm.value = 100 * 10**18
    committed_url = "https://huggingface.co/api/models/open-llm-leaderboard/evals/fake-reasoner-7b"

    chal_id = contract.create_challenge(
        str(direct_bob),
        "REASONING_MATH_MMLU",
        "Achieve MMLU >= 80.0% without benchmark contamination.",
        committed_url,
        30 * 10**18,
        604800,
        "fake-reasoner-7b",
    )

    direct_vm.sender = direct_bob
    direct_vm.value = 30 * 10**18
    contract.stake_and_enter_challenge(chal_id)

    # Mock verified failed evaluation telemetry
    direct_vm.mock_web(
        r".*",
        {
            "status": 200,
            "body": json.dumps({
                "model_id": "fake-reasoner-7b",
                "mmlu_score": 52.4,
                "contamination_detected": True,
                "status": "EVAL_FAILED",
            }),
        },
    )

    direct_vm.mock_llm(
        r".*",
        json.dumps({
            "action_decision": "SLASH_CHALLENGE",
            "confidence_score": 98,
            "benchmark_achieved": False,
            "summary": "Model achieved only 52.4% MMLU against the required 80.0% threshold with clear proof of test-set leakage. Challenge slashed.",
        }),
    )

    direct_vm.sender = direct_alice
    contract.adjudicate_benchmark(
        chal_id,
        "Model failed benchmark evaluation.",
        committed_url,
    )

    c = contract.get_challenge(chal_id)
    assert c["status"] == "SLASHED"
    assert c["adjudication_verdict"] == "SLASH_CHALLENGE"
    assert c["adjudication_confidence"] == 98
    assert c["is_finalized"] is True

    stats = contract.get_protocol_stats()
    assert stats["total_active_liabilities"] == "0", "Liabilities cleanly settled"


def test_in_progress_eval_grace_period_extension(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """Test that queued or in-progress evaluations trigger EXTEND_EVAL_WINDOW without disbursing funds."""
    contract = direct_deploy("contracts/verimodel.py")

    direct_vm.sender = direct_alice
    direct_vm.value = 50 * 10**18
    committed_url = "https://huggingface.co/api/models/open-llm-leaderboard/evals/queued-model"

    chal_id = contract.create_challenge(
        str(direct_bob),
        "SAFETY_ALIGNMENT",
        "Achieve SafetyScore >= 95.0%",
        committed_url,
        15 * 10**18,
        604800,
        "queued-model",
    )

    direct_vm.sender = direct_bob
    direct_vm.value = 15 * 10**18
    contract.stake_and_enter_challenge(chal_id)

    # Mock in-progress evaluation
    direct_vm.mock_web(r".*", {"status": 200, "body": json.dumps({"status": "RUNNING", "progress_pct": 60})})
    direct_vm.mock_llm(
        r".*",
        json.dumps({
            "action_decision": "EXTEND_EVAL_WINDOW",
            "confidence_score": 60,
            "benchmark_achieved": False,
            "summary": "Evaluation job is actively running at 60% completion. Evaluation window extended for final scores.",
        }),
    )

    direct_vm.sender = direct_bob
    contract.adjudicate_benchmark(chal_id, "Job in queue", committed_url)

    c = contract.get_challenge(chal_id)
    assert c["status"] == "ACTIVE"
    assert c["is_finalized"] is False


def test_mismatched_leaderboard_url_reverts(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """Adversarial Test: Verifies that caller cannot pass an uncommitted URL during adjudication."""
    contract = direct_deploy("contracts/verimodel.py")

    direct_vm.sender = direct_alice
    direct_vm.value = 40 * 10**18
    committed_url = "https://huggingface.co/api/models/evals/org/real-model"

    chal_id = contract.create_challenge(
        str(direct_bob),
        "CODING_HUMANEVAL",
        "Pass HumanEval",
        committed_url,
        10 * 10**18,
        604800,
        "real-model",
    )

    direct_vm.sender = direct_bob
    direct_vm.value = 10 * 10**18
    contract.stake_and_enter_challenge(chal_id)

    # Bob attempts to substitute a fake uncommitted URL
    fake_url = "https://huggingface.co/api/models/evals/attacker/fake-model"
    with direct_vm.expect_revert("Mismatched evidence source. Adjudication is strictly bound to committed source"):
        contract.adjudicate_benchmark(chal_id, "Fake run", fake_url)


def test_untrusted_domain_spoofing_reverts(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """Adversarial Test: Verifies that domain substring/prefix spoofing is rejected."""
    contract = direct_deploy("contracts/verimodel.py")

    direct_vm.sender = direct_alice
    direct_vm.value = 30 * 10**18

    # Substring domain spoofing
    with direct_vm.expect_revert("Untrusted evaluation source"):
        contract.create_challenge(
            str(direct_bob),
            "CODING_HUMANEVAL",
            "SLA",
            "https://huggingface.co.attacker.com/evals",
            10 * 10**18,
            604800,
            "spoofed-model",
        )


def test_unauthorized_early_release_reverts(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """Test that active challenges cannot be released early before expiration (Fail-Closed)."""
    contract = direct_deploy("contracts/verimodel.py")

    direct_vm.sender = direct_alice
    direct_vm.value = 30 * 10**18
    committed_url = "https://huggingface.co/api/models/evals/model"

    chal_id = contract.create_challenge(
        str(direct_bob),
        "CODING_HUMANEVAL",
        "Spec",
        committed_url,
        10 * 10**18,
        604800,  # 7 days
        "model",
    )

    # Sponsor attempts early release while coverage is active
    with direct_vm.expect_revert("Challenge evaluation window is still active. Cannot release before expiration timestamp."):
        contract.release_expired_unclaimed_challenge(chal_id)


def test_non_developer_stake_reverts(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """Test that arbitrary third parties cannot stake on someone else's challenge."""
    contract = direct_deploy("contracts/verimodel.py")

    direct_vm.sender = direct_alice
    direct_vm.value = 30 * 10**18
    committed_url = "https://huggingface.co/api/models/evals/model"

    chal_id = contract.create_challenge(
        str(direct_bob),
        "CODING_HUMANEVAL",
        "Spec",
        committed_url,
        10 * 10**18,
        604800,
        "model",
    )

    # Charlie (third party) attempts to stake
    direct_charlie = "0x9999999999999999999999999999999999999999"
    direct_vm.sender = direct_charlie
    direct_vm.value = 10 * 10**18

    with direct_vm.expect_revert("Only the designated model developer can deposit challenge stake."):
        contract.stake_and_enter_challenge(chal_id)


def test_missing_or_malformed_http_status_strictly_forces_retry_and_never_slashes(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """
    Steward Verification Test (Gen. Dave):
    Proves that a missing or malformed HTTP response status (e.g. status: None, status: 'invalid')
    strictly terminates before LLM adjudication and returns EXTEND_EVAL_WINDOW (retry outcome),
    preventing any high-confidence slash or unauthorized escrow transfer.
    """
    contract = direct_deploy("contracts/verimodel.py")

    # Step 1: Alice creates challenge with 50 GEN bounty
    direct_vm.sender = direct_alice
    direct_vm.value = 50 * 10**18
    committed_url = "https://huggingface.co/api/models/open-llm-leaderboard/evals/status-edge-model"

    chal_id = contract.create_challenge(
        str(direct_bob),
        "CODING_HUMANEVAL",
        "Achieve HumanEval >= 80.0%",
        committed_url,
        20 * 10**18,
        604800,
        "status-edge-model",
    )

    # Step 2: Bob stakes 20 GEN collateral
    direct_vm.sender = direct_bob
    direct_vm.value = 20 * 10**18
    contract.stake_and_enter_challenge(chal_id)

    stats_before = contract.get_protocol_stats()
    expected_liabilities = str(70 * 10**18)
    assert stats_before["total_active_liabilities"] == expected_liabilities

    # Case A: Missing status (status is None / missing)
    direct_vm.mock_web(
        r".*",
        {"body": "Corrupted response without HTTP status code"},
    )
    direct_vm.sender = direct_alice
    contract.adjudicate_benchmark(chal_id, "Attempt with missing HTTP status", committed_url)

    c_missing = contract.get_challenge(chal_id)
    assert c_missing["status"] == "ACTIVE", "Challenge must remain ACTIVE on missing status"
    assert c_missing["is_finalized"] is False, "Challenge must NOT reach finality"
    assert c_missing["adjudication_verdict"] == "EXTEND_EVAL_WINDOW"
    assert c_missing["adjudication_confidence"] == 0
    assert "Missing HTTP response status" in c_missing["adjudication_summary"]
    assert contract.get_protocol_stats()["total_active_liabilities"] == expected_liabilities

    # Case B: Malformed status (status is non-integer string 'invalid_status')
    direct_vm.mock_web(
        r".*",
        {"status": "invalid_status", "body": "Non-integer status string"},
    )
    direct_vm.sender = direct_bob
    contract.adjudicate_benchmark(chal_id, "Attempt with malformed status string", committed_url)

    c_malformed = contract.get_challenge(chal_id)
    assert c_malformed["status"] == "ACTIVE", "Challenge must remain ACTIVE on malformed status"
    assert c_malformed["is_finalized"] is False
    assert c_malformed["adjudication_verdict"] == "EXTEND_EVAL_WINDOW"
    assert c_malformed["adjudication_confidence"] == 0
    assert "Malformed HTTP response status" in c_malformed["adjudication_summary"]
    assert contract.get_protocol_stats()["total_active_liabilities"] == expected_liabilities


def test_null_or_empty_body_strictly_forces_retry_and_never_slashes(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """
    Test that null/missing or whitespace-only response bodies strictly return EXTEND_EVAL_WINDOW
    and never slash.
    """
    contract = direct_deploy("contracts/verimodel.py")

    direct_vm.sender = direct_alice
    direct_vm.value = 50 * 10**18
    committed_url = "https://huggingface.co/api/models/open-llm-leaderboard/evals/empty-body-model"

    chal_id = contract.create_challenge(
        str(direct_bob),
        "CODING_HUMANEVAL",
        "Achieve HumanEval >= 80.0%",
        committed_url,
        20 * 10**18,
        604800,
        "empty-body-model",
    )

    direct_vm.sender = direct_bob
    direct_vm.value = 20 * 10**18
    contract.stake_and_enter_challenge(chal_id)

    # Mock HTTP 200 with null body
    direct_vm.mock_web(
        r".*",
        {"status": 200, "body": None},
    )
    direct_vm.sender = direct_alice
    contract.adjudicate_benchmark(chal_id, "Attempt with null body", committed_url)

    c_null = contract.get_challenge(chal_id)
    assert c_null["status"] == "ACTIVE"
    assert c_null["is_finalized"] is False
    assert c_null["adjudication_verdict"] == "EXTEND_EVAL_WINDOW"
    assert c_null["adjudication_confidence"] == 0
    assert "null/missing body data" in c_null["adjudication_summary"]

