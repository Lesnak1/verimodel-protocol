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
    )
    assert chal_id == 0

    c_init = contract.get_challenge(chal_id)
    assert c_init["sponsor"].lower() == str(direct_alice).lower()
    assert c_init["model_developer"].lower() == str(direct_bob).lower()
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


def test_benchmark_falsification_slashing(
    direct_vm, direct_deploy, direct_alice, direct_bob
):
    """
    Test adversarial benchmark falsification & developer stake slashing:
    1. Developer claims 85% MMLU but live eval telemetry shows only 52.4% (benchmark contaminated/failed).
    2. Validators verify failure on live telemetry and reach consensus on SLASH_CHALLENGE (conf: 98).
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
    )

    direct_vm.sender = direct_bob
    direct_vm.value = 30 * 10**18
    contract.stake_and_enter_challenge(chal_id)

    # Mock failed evaluation telemetry
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
            "summary": "Model achieved only 52.4% MMLU against the required 80.0% threshold with evidence of test-set leakage. Challenge slashed.",
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
    assert c["is_finalized"] is True


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
    )

    # Charlie (third party) attempts to stake
    direct_charlie = "0x9999999999999999999999999999999999999999"
    direct_vm.sender = direct_charlie
    direct_vm.value = 10 * 10**18

    with direct_vm.expect_revert("Only the designated model developer can deposit challenge stake."):
        contract.stake_and_enter_challenge(chal_id)
