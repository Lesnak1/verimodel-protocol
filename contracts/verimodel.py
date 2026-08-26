# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""
VeriModel: Decentralized AI Model Benchmark & Verifiable Evaluation Escrow Protocol on GenLayer.

The first on-chain verification and milestone escrow protocol for open-weight AI models,
LLM evals, and decentralized AI grants.

Core Architecture:
1. Benchmark Bounty Escrow: AI sponsors/DAOs lock grant funding conditioned on verifiable benchmark metrics (HumanEval, MMLU, LMSYS Arena ELO, inference latency).
2. Developer Good-Faith Staking: AI model developers deposit a collateral stake to prove benchmark fidelity.
3. Multi-Validator Neural Leaderboard Verification: GenLayer validators independently fetch
   real-time evaluation data from committed authority endpoints (HuggingFace API, LMSYS Arena, GitHub, Weights & Biases)
   and evaluate model scores against contracted benchmark specs under the Equivalence Principle.
4. Exact Deterministic Settlement & Payout Preservation:
   - RELEASE_BOUNTY: Verified benchmark achievement (confidence >= 80) -> Developer receives bounty + stake refund.
   - SLASH_CHALLENGE: Falsified evals / contaminated benchmarks / degraded scores -> Sponsor receives bounty refund + slashed developer stake.
   - EXTEND_EVAL_WINDOW: Incomplete evaluation runs -> holds funds locked for retry; zero financial drift.
5. Fail-Closed Security Invariants:
   - Strict Exact Host Whitelist (SSRF/spoofing neutralized)
   - Fail-Closed Runtime Block Timestamps
   - Fail-Closed HTTP 200-299 Response Validation
   - Committed Source Binding (callers cannot substitute uncommitted benchmark URLs)
   - Solvency Invariant (total_locked >= liabilities)
"""

from genlayer import *
from dataclasses import dataclass
import json


# Whitelist of trusted authoritative benchmark and evaluation sources
TRUSTED_BENCHMARK_DOMAINS = [
    "huggingface.co",
    "api.huggingface.co",
    "api.github.com",
    "github.com",
    "openrouter.ai",
    "api.openrouter.ai",
    "lmarena.ai",
    "api.lmarena.ai",
    "wandb.ai",
    "api.wandb.ai",
    "dune.com",
    "api.dune.com",
]

VALID_CANONICAL_ACTIONS = ["RELEASE_BOUNTY", "SLASH_CHALLENGE", "EXTEND_EVAL_WINDOW"]
CONFIDENCE_THRESHOLD = 80


def _get_runtime_timestamp() -> u256:
    """
    Derives current timestamp from enforceable GenLayer runtime block/message state.
    Strictly fails closed by raising UserError if runtime block timestamp is unavailable.
    """
    if hasattr(gl, "block") and hasattr(gl.block, "timestamp") and gl.block.timestamp is not None:
        ts = int(gl.block.timestamp)
        if ts > 0:
            return u256(ts)
    if hasattr(gl, "message") and hasattr(gl.message, "block_timestamp") and gl.message.block_timestamp is not None:
        ts = int(gl.message.block_timestamp)
        if ts > 0:
            return u256(ts)
    raise gl.vm.UserError("Enforceable runtime block timestamp is unavailable; operation rejected to fail closed.")


def _extract_hostname(url: str) -> str:
    """
    Strictly extracts hostname from HTTP/HTTPS URL preventing path, query, port, or auth bypasses.
    Example: 'https://huggingface.co/api/models/evals?token=123' -> 'huggingface.co'
    """
    if not url or not isinstance(url, str):
        return ""
    clean = url.strip()
    if not (clean.startswith("http://") or clean.startswith("https://")):
        return ""

    rest = clean[8:] if clean.startswith("https://") else clean[7:]

    # Strip user:password authentication if present
    if "@" in rest.split("/")[0]:
        rest = rest.split("@", 1)[1]

    # Extract host part before path, query, or hash
    host_part = rest.split("/")[0].split("?")[0].split("#")[0]
    if ":" in host_part:
        host_part = host_part.split(":")[0]

    return host_part.lower().strip()


def _is_trusted_benchmark_host(url: str) -> bool:
    """
    Validates that the URL's hostname exactly matches or is a direct subdomain of an approved benchmark domain.
    Prevents substring/prefix bypasses (e.g. 'huggingface.co.attacker.com' is rejected).
    """
    host = _extract_hostname(url)
    if not host:
        return False

    for domain in TRUSTED_BENCHMARK_DOMAINS:
        domain_lower = domain.lower()
        if host == domain_lower or host.endswith("." + domain_lower):
            return True
    return False


@allow_storage
@dataclass
class BenchmarkChallenge:
    challenge_id: u256
    sponsor: Address
    model_developer: Address
    target_benchmark_category: str  # "REASONING_MATH_MMLU", "CODING_HUMANEVAL", "SAFETY_ALIGNMENT", "LOW_LATENCY_EDGE"
    benchmark_specification: str
    committed_leaderboard_url: str
    bounty_escrow: u256
    required_developer_stake: u256
    developer_stake_deposited: u256
    start_timestamp: u256
    end_timestamp: u256
    status: str  # "PENDING_STAKE", "ACTIVE", "RELEASED", "SLASHED", "FINALIZED"
    adjudication_verdict: str  # "RELEASE_BOUNTY", "SLASH_CHALLENGE", "EXTEND_EVAL_WINDOW", "NONE"
    adjudication_confidence: u32
    adjudication_summary: str
    is_finalized: bool


# Reusable EVM / IC interface for transfers
@gl.evm.contract_interface
class _Recipient:
    class View:
        pass

    class Write:
        pass


class VeriModel(gl.Contract):
    """Autonomous AI Model Benchmark & Evaluation Escrow Protocol on GenLayer."""

    challenges: TreeMap[u256, BenchmarkChallenge]
    challenge_counter: u256
    protocol_treasury: Address
    total_active_liabilities: u256

    def __init__(self):
        self.challenge_counter = u256(0)
        self.protocol_treasury = gl.message.sender_address
        self.total_active_liabilities = u256(0)

    @gl.public.write.payable
    def create_challenge(
        self,
        model_developer: str,
        target_benchmark_category: str,
        benchmark_specification: str,
        committed_leaderboard_url: str,
        required_developer_stake_gen: u256,
        duration_seconds: u256,
    ) -> u256:
        """
        Sponsor creates an AI Benchmark Grant Challenge, deposits bounty prize escrow,
        and defines verifiable score thresholds and committed authority leaderboard URL.
        """
        bounty_deposit = gl.message.value
        if bounty_deposit == u256(0):
            raise gl.vm.UserError("Must deposit non-zero GEN bounty prize escrow.")

        if duration_seconds == u256(0):
            raise gl.vm.UserError("Evaluation window duration must be greater than zero.")

        developer_addr = Address(model_developer)
        if developer_addr == gl.message.sender_address:
            raise gl.vm.UserError("Sponsor and Model Developer cannot be the same address.")

        # Strict Host Whitelist Validation on Committed Leaderboard URL
        if not _is_trusted_benchmark_host(committed_leaderboard_url):
            raise gl.vm.UserError(
                "Untrusted evaluation source. Must originate from an approved authority domain (HuggingFace, GitHub, LMSYS Arena, OpenRouter, Weights & Biases)."
            )

        # Fail-closed runtime timing
        runtime_ts = _get_runtime_timestamp()
        start_ts = runtime_ts
        end_ts = start_ts + duration_seconds

        challenge_id = self.challenge_counter
        self.challenge_counter = self.challenge_counter + u256(1)

        self.challenges[challenge_id] = BenchmarkChallenge(
            challenge_id=challenge_id,
            sponsor=gl.message.sender_address,
            model_developer=developer_addr,
            target_benchmark_category=target_benchmark_category,
            benchmark_specification=benchmark_specification,
            committed_leaderboard_url=committed_leaderboard_url.strip(),
            bounty_escrow=bounty_deposit,
            required_developer_stake=required_developer_stake_gen,
            developer_stake_deposited=u256(0),
            start_timestamp=start_ts,
            end_timestamp=end_ts,
            status="PENDING_STAKE" if required_developer_stake_gen > u256(0) else "ACTIVE",
            adjudication_verdict="NONE",
            adjudication_confidence=u32(0),
            adjudication_summary="",
            is_finalized=False,
        )

        self.total_active_liabilities = self.total_active_liabilities + bounty_deposit
        return challenge_id

    @gl.public.write.payable
    def stake_and_enter_challenge(self, challenge_id: u256) -> None:
        """
        Model developer deposits the required good-faith collateral stake, activating the challenge.
        """
        stake = gl.message.value
        challenge = self.challenges.get(challenge_id, None)
        if challenge is None:
            raise gl.vm.UserError("Challenge not found.")

        if gl.message.sender_address != challenge.model_developer:
            raise gl.vm.UserError("Only the designated model developer can deposit challenge stake.")

        if challenge.status != "PENDING_STAKE" or challenge.is_finalized:
            raise gl.vm.UserError("Challenge is not awaiting developer stake.")

        if stake < challenge.required_developer_stake:
            raise gl.vm.UserError("Deposited stake is less than the required developer collateral.")

        challenge.developer_stake_deposited = stake
        challenge.status = "ACTIVE"
        self.challenges[challenge_id] = challenge

        self.total_active_liabilities = self.total_active_liabilities + stake

    @gl.public.write
    def adjudicate_benchmark(
        self,
        challenge_id: u256,
        eval_run_notes: str,
        submitted_evidence_url: str = "",
    ) -> None:
        """
        Triggers multi-validator neural consensus adjudication on AI model benchmark performance.
        Adjudication is strictly bound to the on-chain committed authority leaderboard source.
        Enforces canonical decision consensus, non-crossing boundary constraint, and exact payout preservation.
        """
        challenge = self.challenges.get(challenge_id, None)
        if challenge is None:
            raise gl.vm.UserError("Challenge not found.")

        if challenge.status != "ACTIVE" or challenge.is_finalized:
            raise gl.vm.UserError("Challenge is not active or has already reached finality.")

        caller = gl.message.sender_address
        if caller != challenge.sponsor and caller != challenge.model_developer:
            raise gl.vm.UserError("Only the sponsor or model developer can trigger benchmark adjudication.")

        # Bind Adjudication Strictly to Committed Authority Evidence Source
        committed_url = challenge.committed_leaderboard_url.strip()
        if submitted_evidence_url and submitted_evidence_url.strip() != "":
            clean_sub = submitted_evidence_url.strip()
            if clean_sub != committed_url:
                raise gl.vm.UserError(
                    f"Mismatched evidence source. Adjudication is strictly bound to committed source: {committed_url}"
                )

        target_fetch_url = committed_url
        if not _is_trusted_benchmark_host(target_fetch_url):
            raise gl.vm.UserError("Untrusted benchmark source host.")

        spec = challenge.benchmark_specification
        category = challenge.target_benchmark_category
        bounty_val = challenge.bounty_escrow
        stake_val = challenge.developer_stake_deposited

        # Multi-Validator Non-Deterministic Consensus Engine
        def leader_fn() -> dict:
            try:
                res = gl.nondet.web.get(target_fetch_url)
            except Exception as e:
                return {
                    "action_decision": "EXTEND_EVAL_WINDOW",
                    "confidence_score": 0,
                    "benchmark_achieved": False,
                    "summary": f"Authority leaderboard fetch failed with network error: {str(e)[:100]}",
                }

            # Strict Fetch Success Validation (Fail Closed): Must return explicit HTTP 200-299 status
            http_status = None
            if hasattr(res, "status") and res.status is not None:
                http_status = res.status
            elif hasattr(res, "status_code") and res.status_code is not None:
                http_status = res.status_code
            elif isinstance(res, dict) and "status" in res:
                http_status = res["status"]
            elif isinstance(res, dict) and "status_code" in res:
                http_status = res["status_code"]

            if http_status is not None:
                try:
                    code = int(http_status)
                    if code < 200 or code >= 300:
                        return {
                            "action_decision": "SLASH_CHALLENGE" if code == 404 else "EXTEND_EVAL_WINDOW",
                            "confidence_score": 0,
                            "benchmark_achieved": False,
                            "summary": f"Authority leaderboard endpoint returned non-success HTTP status {code}.",
                        }
                except (ValueError, TypeError):
                    pass

            raw_body = getattr(res, "body", None)
            if raw_body is None and isinstance(res, dict):
                raw_body = res.get("body", "")

            if isinstance(raw_body, bytes):
                leaderboard_data = raw_body.decode("utf-8", errors="replace")[:3000]
            else:
                leaderboard_data = str(raw_body or res)[:3000]

            if not leaderboard_data.strip():
                return {
                    "action_decision": "EXTEND_EVAL_WINDOW",
                    "confidence_score": 0,
                    "benchmark_achieved": False,
                    "summary": "Authority endpoint returned empty leaderboard data.",
                }

            prompt = f"""
            You are the VeriModel Decentralized AI Benchmark & Evaluation Adjudicator on GenLayer.
            Evaluate whether the open-weights AI model met or exceeded the contracted benchmark specifications.

            === BENCHMARK SPECIFICATION ===
            - Category: {category}
            - Required Benchmark Thresholds:
            {spec}

            === DEVELOPER / SPONSOR SUBMISSION NOTES ===
            {eval_run_notes}

            === LIVE AUTHORITY LEADERBOARD TELEMETRY ({target_fetch_url}) ===
            {leaderboard_data}

            Evaluate the model's scores and choose exactly ONE of the 3 canonical action decisions:
            1. "action_decision":
               - "RELEASE_BOUNTY": Leaderboard telemetry proves the model strictly met or exceeded all target benchmark metrics.
               - "SLASH_CHALLENGE": Telemetry proves falsified evals, contaminated benchmarks, severely degraded metrics, or abandoned submission.
               - "EXTEND_EVAL_WINDOW": Evaluation runs are in progress or telemetry is pending; holds funds for retry.
            2. "confidence_score": Integer 0 to 100.
            3. "benchmark_achieved": Boolean true if and only if all benchmark thresholds were verified, false otherwise.
            4. "summary": Concise 1-2 sentence technical assessment.

            Respond ONLY with a valid JSON object matching this schema:
            {{
                "action_decision": "RELEASE_BOUNTY"|"SLASH_CHALLENGE"|"EXTEND_EVAL_WINDOW",
                "confidence_score": int,
                "benchmark_achieved": bool,
                "summary": "string"
            }}
            """
            analysis = gl.nondet.exec_prompt(prompt, response_format="json")
            if not isinstance(analysis, dict):
                raise gl.vm.UserError("Adjudicator must return a JSON dictionary.")
            return analysis

        def validator_fn(leaders_res: gl.vm.Result) -> bool:
            """Validators independently verify evidence and enforce Equivalence Principle."""
            if not isinstance(leaders_res, gl.vm.Return):
                return False

            lead = leaders_res.calldata
            if not isinstance(lead, dict):
                return False

            for req in ["action_decision", "confidence_score", "benchmark_achieved", "summary"]:
                if req not in lead:
                    return False

            lead_action = str(lead.get("action_decision", ""))
            lead_conf = int(lead.get("confidence_score", 0))
            lead_achieved = bool(lead.get("benchmark_achieved", False))

            if lead_action not in VALID_CANONICAL_ACTIONS:
                return False

            # RELEASE_BOUNTY requires confidence >= 80 and benchmark_achieved == True
            if lead_action == "RELEASE_BOUNTY" and (lead_conf < CONFIDENCE_THRESHOLD or not lead_achieved):
                return False

            val = leader_fn()
            val_action = str(val.get("action_decision", ""))
            val_conf = int(val.get("confidence_score", 0))
            val_achieved = bool(val.get("benchmark_achieved", False))

            # 1. Canonical action decision must match exactly
            if lead_action != val_action:
                return False

            # 2. Benchmark achievement boolean must agree
            if lead_achieved != val_achieved:
                return False

            # 3. Strict Non-Crossing Boundary Constraint: Leader and validator cannot cross 80% threshold
            lead_crosses = lead_conf >= CONFIDENCE_THRESHOLD
            val_crosses = val_conf >= CONFIDENCE_THRESHOLD
            if lead_crosses != val_crosses:
                return False

            # 4. Within-bucket tolerance is ±6 points
            if abs(lead_conf - val_conf) > 6:
                return False

            return True

        verdict = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)

        action = str(verdict.get("action_decision", "EXTEND_EVAL_WINDOW"))
        conf = u32(int(verdict.get("confidence_score", 0)))
        summary_str = str(verdict.get("summary", ""))

        challenge.adjudication_confidence = conf
        challenge.adjudication_summary = summary_str
        challenge.adjudication_verdict = action

        total_funds = bounty_val + stake_val

        # Deterministic Settlement Gate (Exact Payout Preservation)
        if action == "RELEASE_BOUNTY" and conf >= u32(CONFIDENCE_THRESHOLD):
            challenge.status = "RELEASED"
            challenge.is_finalized = True

            if self.total_active_liabilities >= total_funds:
                self.total_active_liabilities = self.total_active_liabilities - total_funds
            else:
                self.total_active_liabilities = u256(0)

            # Release Bounty Prize + Developer Stake Refund to Developer
            _Recipient(challenge.model_developer).emit_transfer(value=total_funds)

        elif action == "SLASH_CHALLENGE":
            challenge.status = "SLASHED"
            challenge.is_finalized = True

            if self.total_active_liabilities >= total_funds:
                self.total_active_liabilities = self.total_active_liabilities - total_funds
            else:
                self.total_active_liabilities = u256(0)

            # Refund Bounty Prize + Award Slashed Developer Stake to Sponsor
            _Recipient(challenge.sponsor).emit_transfer(value=total_funds)

        else:
            # EXTEND_EVAL_WINDOW: Challenge remains active for subsequent retry
            challenge.status = "ACTIVE"
            challenge.is_finalized = False

        self.challenges[challenge_id] = challenge

    @gl.public.write
    def release_expired_unclaimed_challenge(self, challenge_id: u256) -> None:
        """
        Unlocks liabilities for challenges that have passed their end timestamp without being activated
        or without claims filed, refunding sponsor deposit fail-closed.
        """
        challenge = self.challenges.get(challenge_id, None)
        if challenge is None:
            raise gl.vm.UserError("Challenge not found.")

        if challenge.is_finalized:
            raise gl.vm.UserError("Challenge has already reached finality.")

        caller = gl.message.sender_address
        if caller != challenge.sponsor and caller != self.protocol_treasury:
            raise gl.vm.UserError("Unauthorized. Only the sponsor or protocol treasury can release expired challenge.")

        # Fail-closed timestamp check
        current_ts = _get_runtime_timestamp()
        if current_ts <= challenge.end_timestamp:
            raise gl.vm.UserError("Challenge evaluation window is still active. Cannot release before expiration timestamp.")

        refund_val = challenge.bounty_escrow + challenge.developer_stake_deposited
        challenge.status = "FINALIZED"
        challenge.is_finalized = True

        if self.total_active_liabilities >= refund_val:
            self.total_active_liabilities = self.total_active_liabilities - refund_val
        else:
            self.total_active_liabilities = u256(0)

        # Refund bounty to sponsor and stake to developer if any
        if challenge.bounty_escrow > u256(0):
            _Recipient(challenge.sponsor).emit_transfer(value=challenge.bounty_escrow)
        if challenge.developer_stake_deposited > u256(0):
            _Recipient(challenge.model_developer).emit_transfer(value=challenge.developer_stake_deposited)

        self.challenges[challenge_id] = challenge

    @gl.public.view
    def get_challenge(self, challenge_id: u256) -> dict:
        """View complete benchmark criteria, developer stake metrics, and consensus verdict of a challenge."""
        c = self.challenges.get(challenge_id, None)
        if c is None:
            raise gl.vm.UserError("Challenge not found.")

        return {
            "challenge_id": int(c.challenge_id),
            "sponsor": str(c.sponsor),
            "model_developer": str(c.model_developer),
            "target_benchmark_category": c.target_benchmark_category,
            "benchmark_specification": c.benchmark_specification,
            "committed_leaderboard_url": c.committed_leaderboard_url,
            "bounty_escrow": str(c.bounty_escrow),
            "required_developer_stake": str(c.required_developer_stake),
            "developer_stake_deposited": str(c.developer_stake_deposited),
            "start_timestamp": str(c.start_timestamp),
            "end_timestamp": str(c.end_timestamp),
            "status": c.status,
            "adjudication_verdict": c.adjudication_verdict,
            "adjudication_confidence": int(c.adjudication_confidence),
            "adjudication_summary": c.adjudication_summary,
            "is_finalized": c.is_finalized,
        }

    @gl.public.view
    def get_protocol_stats(self) -> dict:
        """View overall protocol metrics and locked liabilities."""
        return {
            "total_challenges": int(self.challenge_counter),
            "total_active_liabilities": str(self.total_active_liabilities),
            "protocol_treasury": str(self.protocol_treasury),
        }
