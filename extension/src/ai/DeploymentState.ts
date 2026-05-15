// Pure-function FSM for the deployment assistant.
// No I/O, no chrome.storage, no React — only state + events.

export type CollectedInfo = {
  ecsHost?: string;
  ecsUser?: string;
  sshPrivateKey?: string; // marker — never logged
  gitRepoUrl?: string | null; // null = use demo
  dashscopeApiKey?: string;
};

export type DeploymentState =
  | { phase: 'gathering_deepseek_key' }
  | { phase: 'choosing_path' }
  | { phase: 'collecting_info'; path: 'local' | 'ecs'; collected: Partial<CollectedInfo> }
  | { phase: 'executing'; path: 'local' | 'ecs'; collected: CollectedInfo; currentStep: string }
  | { phase: 'verifying'; path: 'local' | 'ecs'; orchestratorUrl: string; adminToken: string }
  | { phase: 'done'; completedAt: number };

export type DeploymentEvent =
  | { type: 'deepseek_key_set' }
  | { type: 'path_chosen'; path: 'local' | 'ecs' }
  | { type: 'collect'; patch: Partial<CollectedInfo> }
  | { type: 'collection_complete'; collected: CollectedInfo; firstStep: string }
  | { type: 'step_advance'; nextStep: string }
  | { type: 'execution_done'; orchestratorUrl: string; adminToken: string }
  | { type: 'verification_passed'; completedAt: number }
  | { type: 'reset' };

export type TransitionResult = DeploymentState | { error: string };

export function initialState(): DeploymentState {
  return { phase: 'gathering_deepseek_key' };
}

export function isTerminal(state: DeploymentState): boolean {
  return state.phase === 'done';
}

function illegal(phase: string, eventType: string): { error: string } {
  return { error: `illegal transition: ${eventType} from ${phase}` };
}

export function transition(state: DeploymentState, event: DeploymentEvent): TransitionResult {
  // reset is always legal from any phase
  if (event.type === 'reset') {
    return initialState();
  }

  switch (state.phase) {
    case 'gathering_deepseek_key':
      if (event.type === 'deepseek_key_set') {
        return { phase: 'choosing_path' };
      }
      return illegal(state.phase, event.type);

    case 'choosing_path':
      if (event.type === 'path_chosen') {
        return { phase: 'collecting_info', path: event.path, collected: {} };
      }
      return illegal(state.phase, event.type);

    case 'collecting_info':
      if (event.type === 'collect') {
        return {
          phase: 'collecting_info',
          path: state.path,
          collected: { ...state.collected, ...event.patch },
        };
      }
      if (event.type === 'collection_complete') {
        return {
          phase: 'executing',
          path: state.path,
          collected: event.collected,
          currentStep: event.firstStep,
        };
      }
      return illegal(state.phase, event.type);

    case 'executing':
      if (event.type === 'step_advance') {
        return { ...state, currentStep: event.nextStep };
      }
      if (event.type === 'execution_done') {
        return {
          phase: 'verifying',
          path: state.path,
          orchestratorUrl: event.orchestratorUrl,
          adminToken: event.adminToken,
        };
      }
      return illegal(state.phase, event.type);

    case 'verifying':
      if (event.type === 'verification_passed') {
        return { phase: 'done', completedAt: event.completedAt };
      }
      return illegal(state.phase, event.type);

    case 'done':
      return illegal(state.phase, event.type);
  }
}
