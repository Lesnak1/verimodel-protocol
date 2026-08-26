import { createClient, createAccount, generatePrivateKey, type Address } from 'genlayer-js';
import { testnetBradbury, studionet, localnet } from 'genlayer-js/chains';

/**
 * VeriModel GenLayer Client Integration SDK
 * Complete TypeScript bindings for all intelligent contract methods on GenLayer:
 * - create_challenge (Sponsor deposits benchmark grant bounty and defines score thresholds)
 * - stake_and_enter_challenge (Developer deposits collateral stake to activate challenge)
 * - adjudicate_benchmark (Triggers multi-validator neural consensus over live authority leaderboards)
 * - release_expired_unclaimed_challenge (Unlocks expired challenges fail-closed)
 * - get_challenge (Read-only view of benchmark state, required metrics, and consensus verdict)
 * - get_protocol_stats (Read-only view of active challenges and locked liabilities)
 */

export const DEFAULT_VERIMODEL_ADDRESS: Address = '0xB8e1c3559B66B1b1d7d0823FBEB5A967732e999';

export interface ChallengeState {
  challenge_id: number;
  sponsor: string;
  model_developer: string;
  target_benchmark_category: string;
  benchmark_specification: string;
  committed_leaderboard_url: string;
  bounty_escrow: string;
  required_developer_stake: string;
  developer_stake_deposited: string;
  start_timestamp: string;
  end_timestamp: string;
  status: 'PENDING_STAKE' | 'ACTIVE' | 'RELEASED' | 'SLASHED' | 'FINALIZED';
  adjudication_verdict: string;
  adjudication_confidence: number;
  adjudication_summary: string;
  is_finalized: bool;
}

export interface ProtocolStats {
  total_challenges: number;
  total_active_liabilities: string;
  protocol_treasury: string;
}

export type SupportedChain = 'testnetBradbury' | 'studionet' | 'localnet';

export function getChainConfig(chainType: SupportedChain = 'testnetBradbury') {
  switch (chainType) {
    case 'studionet':
      return studionet;
    case 'localnet':
      return localnet;
    case 'testnetBradbury':
    default:
      return testnetBradbury;
  }
}

export function getGenLayerClient(
  privateKey?: `0x${string}`,
  chainType: SupportedChain = 'testnetBradbury'
) {
  const account = privateKey ? createAccount(privateKey) : createAccount(generatePrivateKey());
  const chain = getChainConfig(chainType);

  return createClient({
    chain,
    account,
  });
}

/**
 * Creates an AI Benchmark Grant Challenge and deposits bounty escrow.
 */
export async function createChallenge(
  client: ReturnType<typeof getGenLayerClient>,
  contractAddress: Address,
  modelDeveloper: Address,
  targetCategory: string,
  benchmarkSpecification: string,
  committedLeaderboardUrl: string,
  requiredDeveloperStakeGen: string | number,
  durationSeconds: number,
  bountyDepositGen: string | number
): Promise<`0x${string}`> {
  const bountyWei = BigInt(Math.floor(Number(bountyDepositGen) * 1e18));
  const stakeWei = BigInt(Math.floor(Number(requiredDeveloperStakeGen) * 1e18));

  const txHash = await client.writeContract({
    address: contractAddress,
    functionName: 'create_challenge',
    args: [
      modelDeveloper,
      targetCategory,
      benchmarkSpecification,
      committedLeaderboardUrl,
      stakeWei,
      BigInt(durationSeconds),
    ],
    value: bountyWei,
  });

  return txHash as `0x${string}`;
}

/**
 * Model developer deposits collateral stake to activate challenge.
 */
export async function stakeAndEnterChallenge(
  client: ReturnType<typeof getGenLayerClient>,
  contractAddress: Address,
  challengeId: bigint | number,
  stakeGenAmount: string | number
): Promise<`0x${string}`> {
  const stakeWei = BigInt(Math.floor(Number(stakeGenAmount) * 1e18));

  const txHash = await client.writeContract({
    address: contractAddress,
    functionName: 'stake_and_enter_challenge',
    args: [BigInt(challengeId)],
    value: stakeWei,
  });

  return txHash as `0x${string}`;
}

/**
 * Evaluates live benchmark telemetry and triggers multi-validator neural consensus adjudication.
 */
export async function adjudicateBenchmark(
  client: ReturnType<typeof getGenLayerClient>,
  contractAddress: Address,
  challengeId: bigint | number,
  evalRunNotes: string,
  submittedEvidenceUrl: string = ''
): Promise<`0x${string}`> {
  const txHash = await client.writeContract({
    address: contractAddress,
    functionName: 'adjudicate_benchmark',
    args: [BigInt(challengeId), evalRunNotes, submittedEvidenceUrl],
    value: BigInt(0),
  });

  return txHash as `0x${string}`;
}

/**
 * Releases expired challenge fail-closed.
 */
export async function releaseExpiredChallenge(
  client: ReturnType<typeof getGenLayerClient>,
  contractAddress: Address,
  challengeId: bigint | number
): Promise<`0x${string}`> {
  const txHash = await client.writeContract({
    address: contractAddress,
    functionName: 'release_expired_unclaimed_challenge',
    args: [BigInt(challengeId)],
    value: BigInt(0),
  });

  return txHash as `0x${string}`;
}

/**
 * Queries challenge details from contract storage.
 */
export async function getChallenge(
  client: ReturnType<typeof getGenLayerClient>,
  contractAddress: Address,
  challengeId: bigint | number
): Promise<ChallengeState> {
  const data = await client.readContract({
    address: contractAddress,
    functionName: 'get_challenge',
    args: [BigInt(challengeId)],
  });

  return data as unknown as ChallengeState;
}

/**
 * Queries protocol-wide statistics.
 */
export async function getProtocolStats(
  client: ReturnType<typeof getGenLayerClient>,
  contractAddress: Address
): Promise<ProtocolStats> {
  const data = await client.readContract({
    address: contractAddress,
    functionName: 'get_protocol_stats',
    args: [],
  });

  return data as unknown as ProtocolStats;
}
