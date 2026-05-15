import { describe, it, expect } from 'vitest';
import {
  initialState,
  transition,
  isTerminal,
  type DeploymentState,
  type DeploymentEvent,
  type CollectedInfo,
} from '../src/ai/DeploymentState';

// ─── helpers ────────────────────────────────────────────────────────────────

function choosingPath(): DeploymentState {
  return transition(initialState(), { type: 'deepseek_key_set' }) as DeploymentState;
}

function collectingInfoLocal(): DeploymentState {
  return transition(choosingPath(), { type: 'path_chosen', path: 'local' }) as DeploymentState;
}

function collectingInfoEcs(): DeploymentState {
  return transition(choosingPath(), { type: 'path_chosen', path: 'ecs' }) as DeploymentState;
}

const fullCollected: CollectedInfo = {
  ecsHost: '1.2.3.4',
  ecsUser: 'root',
  sshPrivateKey: '-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----',
  gitRepoUrl: 'https://github.com/example/repo',
  dashscopeApiKey: 'sk-abc',
};

function executingState(): DeploymentState {
  const ci = collectingInfoEcs();
  return transition(ci, {
    type: 'collection_complete',
    collected: fullCollected,
    firstStep: 'install-docker',
  }) as DeploymentState;
}

function verifyingState(): DeploymentState {
  const ex = executingState();
  return transition(ex, {
    type: 'execution_done',
    orchestratorUrl: 'http://1.2.3.4:8080',
    adminToken: 'tok-secret',
  }) as DeploymentState;
}

// ─── Legal transitions ───────────────────────────────────────────────────────

describe('legal transitions', () => {
  it('deepseek_key_set: gathering_deepseek_key → choosing_path', () => {
    const next = transition(initialState(), { type: 'deepseek_key_set' });
    expect(next).toEqual({ phase: 'choosing_path' });
  });

  it('path_chosen local: choosing_path → collecting_info with empty collected', () => {
    const next = transition(choosingPath(), { type: 'path_chosen', path: 'local' });
    expect(next).toEqual({ phase: 'collecting_info', path: 'local', collected: {} });
  });

  it('path_chosen ecs: choosing_path → collecting_info with empty collected', () => {
    const next = transition(choosingPath(), { type: 'path_chosen', path: 'ecs' });
    expect(next).toEqual({ phase: 'collecting_info', path: 'ecs', collected: {} });
  });

  it('collect: collecting_info merges patch shallowly into collected', () => {
    const ci = collectingInfoEcs();
    const next1 = transition(ci, { type: 'collect', patch: { ecsHost: '1.2.3.4' } }) as DeploymentState;
    expect(next1).toMatchObject({ phase: 'collecting_info', collected: { ecsHost: '1.2.3.4' } });

    // second collect preserves prior fields
    const next2 = transition(next1, { type: 'collect', patch: { ecsUser: 'root' } }) as DeploymentState;
    expect(next2).toMatchObject({
      phase: 'collecting_info',
      collected: { ecsHost: '1.2.3.4', ecsUser: 'root' },
    });
  });

  it('collection_complete: collecting_info → executing', () => {
    const ci = collectingInfoEcs();
    const next = transition(ci, {
      type: 'collection_complete',
      collected: fullCollected,
      firstStep: 'install-docker',
    });
    expect(next).toEqual({
      phase: 'executing',
      path: 'ecs',
      collected: fullCollected,
      currentStep: 'install-docker',
    });
  });

  it('step_advance: executing → executing with updated currentStep', () => {
    const ex = executingState();
    const next = transition(ex, { type: 'step_advance', nextStep: 'start-orchestrator' });
    expect(next).toMatchObject({ phase: 'executing', currentStep: 'start-orchestrator' });
  });

  it('execution_done: executing → verifying', () => {
    const ex = executingState();
    const next = transition(ex, {
      type: 'execution_done',
      orchestratorUrl: 'http://1.2.3.4:8080',
      adminToken: 'tok-secret',
    });
    expect(next).toEqual({
      phase: 'verifying',
      path: 'ecs',
      orchestratorUrl: 'http://1.2.3.4:8080',
      adminToken: 'tok-secret',
    });
  });

  it('verification_passed: verifying → done', () => {
    const vr = verifyingState();
    const ts = 1_700_000_000_000;
    const next = transition(vr, { type: 'verification_passed', completedAt: ts });
    expect(next).toEqual({ phase: 'done', completedAt: ts });
  });
});

// ─── reset from every phase ─────────────────────────────────────────────────

describe('reset returns initialState from every phase', () => {
  const phases: DeploymentState[] = [
    choosingPath(),
    collectingInfoLocal(),
    executingState(),
    verifyingState(),
    { phase: 'done', completedAt: 0 },
  ];

  for (const state of phases) {
    it(`reset from ${state.phase}`, () => {
      const next = transition(state, { type: 'reset' });
      expect(next).toEqual(initialState());
    });
  }

  it('reset from gathering_deepseek_key itself', () => {
    const next = transition(initialState(), { type: 'reset' });
    expect(next).toEqual(initialState());
  });
});

// ─── isTerminal ─────────────────────────────────────────────────────────────

describe('isTerminal', () => {
  it('returns true only for done phase', () => {
    expect(isTerminal({ phase: 'done', completedAt: 0 })).toBe(true);
  });

  it('returns false for gathering_deepseek_key', () => {
    expect(isTerminal(initialState())).toBe(false);
  });

  it('returns false for choosing_path', () => {
    expect(isTerminal(choosingPath())).toBe(false);
  });

  it('returns false for collecting_info', () => {
    expect(isTerminal(collectingInfoLocal())).toBe(false);
  });

  it('returns false for executing', () => {
    expect(isTerminal(executingState())).toBe(false);
  });

  it('returns false for verifying', () => {
    expect(isTerminal(verifyingState())).toBe(false);
  });
});

// ─── Illegal transitions ─────────────────────────────────────────────────────

describe('illegal transitions return { error }', () => {
  function expectError(state: DeploymentState, event: DeploymentEvent) {
    const result = transition(state, event);
    expect(result).toHaveProperty('error');
    expect((result as { error: string }).error).toMatch(/illegal transition/);
    expect((result as { error: string }).error).toContain(event.type);
    expect((result as { error: string }).error).toContain(state.phase);
  }

  it('step_advance from choosing_path is illegal', () => {
    expectError(choosingPath(), { type: 'step_advance', nextStep: 'x' });
  });

  it('execution_done from choosing_path is illegal', () => {
    expectError(choosingPath(), { type: 'execution_done', orchestratorUrl: 'u', adminToken: 't' });
  });

  it('path_chosen from gathering_deepseek_key is illegal', () => {
    expectError(initialState(), { type: 'path_chosen', path: 'local' });
  });

  it('collection_complete from choosing_path is illegal', () => {
    expectError(choosingPath(), {
      type: 'collection_complete',
      collected: fullCollected,
      firstStep: 's',
    });
  });

  it('deepseek_key_set from choosing_path is illegal', () => {
    expectError(choosingPath(), { type: 'deepseek_key_set' });
  });

  it('verification_passed from executing is illegal', () => {
    expectError(executingState(), { type: 'verification_passed', completedAt: 0 });
  });

  it('collect from executing is illegal', () => {
    expectError(executingState(), { type: 'collect', patch: { ecsHost: '1.2.3.4' } });
  });

  it('step_advance from done is illegal', () => {
    expectError({ phase: 'done', completedAt: 0 }, { type: 'step_advance', nextStep: 'x' });
  });

  it('path_chosen from done is illegal', () => {
    expectError({ phase: 'done', completedAt: 0 }, { type: 'path_chosen', path: 'ecs' });
  });

  it('execution_done from collecting_info is illegal', () => {
    expectError(collectingInfoEcs(), {
      type: 'execution_done',
      orchestratorUrl: 'u',
      adminToken: 't',
    });
  });
});

// ─── Immutability ────────────────────────────────────────────────────────────

describe('immutability', () => {
  it('collect does not mutate the original state', () => {
    const ci = collectingInfoEcs() as Extract<DeploymentState, { phase: 'collecting_info' }>;
    const originalCollected = { ...ci.collected };
    transition(ci, { type: 'collect', patch: { ecsHost: 'mutated' } });
    expect(ci.collected).toEqual(originalCollected);
  });

  it('step_advance does not mutate the executing state', () => {
    const ex = executingState() as Extract<DeploymentState, { phase: 'executing' }>;
    const originalStep = ex.currentStep;
    transition(ex, { type: 'step_advance', nextStep: 'mutated-step' });
    expect(ex.currentStep).toBe(originalStep);
  });
});
