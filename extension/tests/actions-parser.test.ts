// Plan 7 · Task 2: parseActionsFromAssistant contract tests.
// All tests follow AAA (Arrange-Act-Assert) and are independent (no shared state).
import { describe, expect, it } from 'vitest';
import {
  AiActionSchema,
  type AiAction,
  type ParseResult,
  parseActionsFromAssistant,
} from '../src/ai/actions';

// ---------------------------------------------------------------------------
// 1. Block at end, valid JSON, 3 different action types → all parsed, prose stripped
// ---------------------------------------------------------------------------
describe('parseActionsFromAssistant · valid block at end', () => {
  it('parses 3 different action types and strips the block from prose', () => {
    // Arrange
    const actions = [
      { type: 'copy_command', label: 'Run install', command: 'npm install', expectsOutput: false },
      { type: 'open_url', label: 'Visit docs', url: 'https://example.com' },
      { type: 'request_output', placeholder: 'Paste the output here' },
    ];
    const text = `Here are your steps:\n\n<actions>${JSON.stringify(actions)}</actions>`;

    // Act
    const result: ParseResult = parseActionsFromAssistant(text);

    // Assert
    expect(result.parseError).toBeUndefined();
    expect(result.actions).toHaveLength(3);
    expect(result.actions[0]).toEqual(actions[0]);
    expect(result.actions[1]).toEqual(actions[1]);
    expect(result.actions[2]).toEqual(actions[2]);
    // Block must be stripped and prose trimmed
    expect(result.prose).toBe('Here are your steps:');
    expect(result.prose).not.toContain('<actions>');
  });
});

// ---------------------------------------------------------------------------
// 2. Prose without block → prose unchanged, actions=[]
// ---------------------------------------------------------------------------
describe('parseActionsFromAssistant · no block', () => {
  it('returns unchanged prose and empty actions when no <actions> block exists', () => {
    // Arrange
    const text = 'Just a plain text reply with no actions block.';

    // Act
    const result = parseActionsFromAssistant(text);

    // Assert
    expect(result.prose).toBe(text);
    expect(result.actions).toEqual([]);
    expect(result.parseError).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// 3. Empty block <actions>[]</actions> → actions=[], no parseError
// ---------------------------------------------------------------------------
describe('parseActionsFromAssistant · empty block', () => {
  it('returns empty actions array with no parseError for <actions>[]</actions>', () => {
    // Arrange
    const text = 'Great, no actions needed right now.<actions>[]</actions>';

    // Act
    const result = parseActionsFromAssistant(text);

    // Assert
    expect(result.actions).toEqual([]);
    expect(result.parseError).toBeUndefined();
    expect(result.prose).not.toContain('<actions>');
    expect(result.prose).toBe('Great, no actions needed right now.');
  });
});

// ---------------------------------------------------------------------------
// 4. JSON syntax error → parseError set, prose has block stripped, actions=[]
// ---------------------------------------------------------------------------
describe('parseActionsFromAssistant · JSON syntax error', () => {
  it('sets parseError and returns empty actions when JSON is malformed', () => {
    // Arrange
    const text = 'Step one:\n<actions>{not valid json[}</actions>';

    // Act
    const result = parseActionsFromAssistant(text);

    // Assert
    expect(result.parseError).toBeDefined();
    expect(typeof result.parseError).toBe('string');
    expect(result.parseError!.length).toBeGreaterThan(0);
    expect(result.actions).toEqual([]);
    // Block should still be stripped from prose
    expect(result.prose).not.toContain('<actions>');
    expect(result.prose.trim()).toBe('Step one:');
  });
});

// ---------------------------------------------------------------------------
// 5. Valid JSON but wrong action shape → parseError set, actions=[]
// ---------------------------------------------------------------------------
describe('parseActionsFromAssistant · schema mismatch', () => {
  it('sets parseError when action type is copy_command but required fields are missing', () => {
    // Arrange — type is valid but label/command/expectsOutput are missing
    const badActions = [{ type: 'copy_command' }];
    const text = `Do this:<actions>${JSON.stringify(badActions)}</actions>`;

    // Act
    const result = parseActionsFromAssistant(text);

    // Assert
    expect(result.parseError).toBeDefined();
    expect(result.actions).toEqual([]);
    expect(result.prose).not.toContain('<actions>');
  });

  it('sets parseError when action type is completely unknown', () => {
    // Arrange
    const badActions = [{ type: 'fly_rocket', destination: 'Mars' }];
    const text = `<actions>${JSON.stringify(badActions)}</actions>`;

    // Act
    const result = parseActionsFromAssistant(text);

    // Assert
    expect(result.parseError).toBeDefined();
    expect(result.actions).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// 6. Two <actions> blocks → only the LAST one is parsed; block stripped from prose
// ---------------------------------------------------------------------------
describe('parseActionsFromAssistant · two blocks', () => {
  it('uses only the last <actions> block and strips it from prose', () => {
    // Arrange
    const firstActions = [{ type: 'open_url', label: 'Old step', url: 'https://old.com' }];
    const lastActions = [
      { type: 'copy_command', label: 'Final step', command: 'echo done', expectsOutput: false },
    ];
    const text = [
      'First block:',
      `<actions>${JSON.stringify(firstActions)}</actions>`,
      'Second block:',
      `<actions>${JSON.stringify(lastActions)}</actions>`,
    ].join('\n');

    // Act
    const result = parseActionsFromAssistant(text);

    // Assert — only last block actions are returned
    expect(result.parseError).toBeUndefined();
    expect(result.actions).toHaveLength(1);
    const action = result.actions[0] as AiAction & { type: 'copy_command' };
    expect(action.type).toBe('copy_command');
    expect(action.command).toBe('echo done');
    // No <actions> tags remain in prose
    expect(result.prose).not.toContain('<actions>');
  });
});

// ---------------------------------------------------------------------------
// 7. AiActionSchema validates all 6 action variants
// ---------------------------------------------------------------------------
describe('AiActionSchema · discriminated union coverage', () => {
  const valid: AiAction[] = [
    { type: 'copy_command', label: 'A', command: 'ls', expectsOutput: true },
    { type: 'open_url', label: 'B', url: 'https://example.com' },
    { type: 'capture_field', field: 'ecsHost', value: '1.2.3.4' },
    { type: 'request_output', placeholder: 'Paste output...' },
    { type: 'validate', kind: 'orchestrator_healthz', url: 'http://localhost:9000' },
    { type: 'validate', kind: 'admin_config', url: 'http://localhost:9000', token: 'tok' },
    { type: 'transition', to: 'choosing_path' },
  ];

  for (const action of valid) {
    it(`accepts valid ${action.type} action`, () => {
      expect(() => AiActionSchema.parse(action)).not.toThrow();
    });
  }

  it('rejects an action with an unknown type', () => {
    expect(() => AiActionSchema.parse({ type: 'unknown_type' })).toThrow();
  });
});

// ---------------------------------------------------------------------------
// 8. Multiline JSON inside actions block (s flag coverage)
// ---------------------------------------------------------------------------
describe('parseActionsFromAssistant · multiline block', () => {
  it('parses actions when JSON spans multiple lines', () => {
    // Arrange
    const text = `Sure!\n<actions>[\n  {\n    "type": "open_url",\n    "label": "Docs",\n    "url": "https://docs.example.com"\n  }\n]</actions>`;

    // Act
    const result = parseActionsFromAssistant(text);

    // Assert
    expect(result.parseError).toBeUndefined();
    expect(result.actions).toHaveLength(1);
    expect(result.actions[0].type).toBe('open_url');
    expect(result.prose).not.toContain('<actions>');
  });
});
