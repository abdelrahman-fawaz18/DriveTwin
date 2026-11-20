import Ajv2020, { type AnySchema, type ValidateFunction } from 'ajv/dist/2020';
import addFormats from 'ajv-formats';
import { createScanner, parseTree, SyntaxKind, type Node as JsonNode, type ParseError } from 'jsonc-parser';

type ObjectValue = Record<string, unknown>;
export interface Bundle {
  profile: { max_payload_bytes: number; max_depth: number; max_safe_integer: number;
    topic_namespace?: string; source_in_topic?: boolean; sample_quality_states?: string[] };
  schemas: Record<string, AnySchema>;
  portableSchema: AnySchema;
  catalogSchema: AnySchema;
  catalogSchemas?: Record<string, AnySchema>;
  catalog: unknown;
}
interface Catalog {
  catalog_version: string;
  assets: Record<string, { sources: string[] }>;
  signals: Record<string, { unit: string; minimum: number; maximum: number }>;
}
export class Rejected extends Error {
  constructor(public readonly reason: string) { super(reason); }
}
function object(value: unknown): ObjectValue {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new Rejected('envelope_schema');
  return value as ObjectValue;
}
function string(value: unknown): string {
  if (typeof value !== 'string') throw new Rejected('envelope_schema');
  return value;
}
function checkPortable(value: unknown, profile: Bundle['profile'], depth = 0): void {
  if (typeof value === 'string') {
    for (const character of value) {
      const point = character.codePointAt(0)!;
      if (point >= 0xD800 && point <= 0xDFFF) throw new Rejected('invalid_json');
    }
  } else if (typeof value === 'number') {
    if (!Number.isFinite(value) || (Number.isInteger(value) && Math.abs(value) > profile.max_safe_integer)) throw new Rejected('portable_json');
  } else if (typeof value === 'object' && value !== null) {
    if (depth + 1 > profile.max_depth) throw new Rejected('invalid_json');
    if (Array.isArray(value)) for (const item of value) checkPortable(item, profile, depth + 1);
    else for (const [key, item] of Object.entries(value)) {
      checkPortable(key, profile, depth + 1); checkPortable(item, profile, depth + 1);
    }
  }
}
function checkDuplicates(node: JsonNode): void {
  if (node.type === 'object') {
    const keys = new Set<string>();
    for (const property of node.children ?? []) {
      const key = String(property.children?.[0]?.value);
      if (keys.has(key)) throw new Rejected('invalid_json');
      keys.add(key);
    }
  }
  for (const child of node.children ?? []) checkDuplicates(child);
}
function decode(raw: Uint8Array, profile: Bundle['profile']): unknown {
  if (raw.byteLength > profile.max_payload_bytes) throw new Rejected('payload_too_large');
  try {
    if (raw[0] === 0xEF && raw[1] === 0xBB && raw[2] === 0xBF) throw new Error('BOM');
    const text = new TextDecoder('utf-8', { fatal: true }).decode(raw);
    const scanner = createScanner(text, false);
    let depth = 0;
    for (let token = scanner.scan(); token !== SyntaxKind.EOF; token = scanner.scan()) {
      if (token === SyntaxKind.OpenBraceToken || token === SyntaxKind.OpenBracketToken) depth++;
      if (depth > profile.max_depth) throw new Error('depth');
      if (token === SyntaxKind.CloseBraceToken || token === SyntaxKind.CloseBracketToken) depth--;
    }
    const errors: ParseError[] = [];
    const tree = parseTree(text, errors, { disallowComments: true, allowTrailingComma: false, allowEmptyContent: false });
    if (!tree || errors.length) throw new Error('syntax');
    checkDuplicates(tree);
    const value: unknown = JSON.parse(text);
    checkPortable(value, profile);
    return value;
  } catch (error) {
    if (error instanceof Rejected) throw error;
    throw new Rejected('invalid_json');
  }
}

export class Contract {
  private readonly validators: Record<string, ValidateFunction> = Object.create(null) as Record<string, ValidateFunction>;
  private readonly portableValidator: ValidateFunction;
  private readonly catalog: Catalog;
  private readonly bundle: Bundle;
  constructor(bundle: Bundle) {
    this.bundle = structuredClone(bundle);
    bundle = this.bundle;
    const ajv = new Ajv2020({ strict: true, strictNumbers: true, coerceTypes: false,
      useDefaults: false, removeAdditional: false, allErrors: false });
    addFormats(ajv, { mode: 'full' });
    const catalogValidator = ajv.compile(bundle.catalogSchema);
    try {
      checkPortable(bundle.catalog, bundle.profile);
      if (!catalogValidator(bundle.catalog)) throw new Error('catalog schema');
      const catalog = structuredClone(bundle.catalog) as Catalog;
      if (Object.values(catalog.signals).some(spec => spec.minimum > spec.maximum)) throw new Error('bounds');
      this.catalog = catalog;
    } catch { throw new Error('Invalid catalog configuration'); }
    for (const [version, schema] of Object.entries(bundle.schemas)) this.validators[version] = ajv.compile(schema);
    this.portableValidator = ajv.compile(bundle.portableSchema);
  }
  validate(raw: Uint8Array, topic: string, expectedRun: string): unknown {
    const value = decode(raw, this.bundle.profile);
    const obj = object(value);
    const version = obj['schema_version'];
    if (typeof version !== 'string' || !Object.hasOwn(this.validators, version)) throw new Rejected('envelope_schema');
    if (!this.validators[version]!(value) || !this.portableValidator(value)) throw new Rejected('envelope_schema');
    if (obj['catalog_version'] !== this.catalog.catalog_version) throw new Rejected('catalog_version');
    if (obj['run_id'] !== expectedRun) throw new Rejected('run_mismatch');
    const assetId = string(obj['asset_id']);
    const namespace = this.bundle.profile.topic_namespace ?? 'qualification';
    const source = this.bundle.profile.source_in_topic ? '/' + string(obj['source_id']) : '';
    if (topic !== `evdt/${namespace}/${expectedRun}/${assetId}${source}/observations`) throw new Rejected('topic_identity');
    const asset = Object.hasOwn(this.catalog.assets, assetId) ? this.catalog.assets[assetId] : undefined;
    if (!asset || !asset.sources.includes(string(obj['source_id']))) throw new Rejected('unregistered_source');
    const seen = new Set<string>();
    const samples = obj['samples'];
    if (!Array.isArray(samples)) throw new Rejected('envelope_schema');
    for (const item of samples) {
      const sample = object(item);
      const key = string(sample['signal_id']);
      const spec = Object.hasOwn(this.catalog.signals, key) ? this.catalog.signals[key] : undefined;
      if (!spec) throw new Rejected('unregistered_signal');
      if (seen.has(key)) throw new Rejected('duplicate_signal');
      seen.add(key);
      if (sample['unit'] !== spec.unit) throw new Rejected('wrong_unit');
      if (sample['quality'] !== 'good' && this.bundle.profile.sample_quality_states) continue;
      const number = sample['value'];
      if (typeof number !== 'number') throw new Rejected('envelope_schema');
      if (number < spec.minimum || number > spec.maximum) throw new Rejected('out_of_range');
    }
    return value;
  }
  forLegacy(raw: Uint8Array, topic: string, expectedRun: string): Uint8Array {
    const result = structuredClone(object(this.validate(raw, topic, expectedRun)));
    delete result['annotations']; result['schema_version'] = '1.0.0';
    const encoded = new TextEncoder().encode(JSON.stringify(result));
    this.validate(encoded, topic, expectedRun);
    return encoded;
  }
}

// Registered catalogs are trusted deployment inputs, never supplied by a message.
export class ContractRegistry {
  private readonly readers: Record<string, Contract> = Object.create(null) as Record<string, Contract>;
  private readonly fallback: Contract;
  private readonly profile: Bundle['profile'];
  constructor(bundle: Bundle, catalogs: unknown[]) {
    this.profile = structuredClone(bundle.profile);
    this.fallback = new Contract(bundle);
    for (const catalog of catalogs) {
      const version = string(object(catalog)['catalog_version']);
      if (Object.hasOwn(this.readers, version)) throw new Error('Duplicate catalog version');
      this.readers[version] = new Contract({...bundle, catalogSchema: bundle.catalogSchemas?.[version] ?? bundle.catalogSchema, catalog});
    }
  }
  validate(raw: Uint8Array, topic: string, expectedRun: string): unknown {
    const obj = object(decode(raw, this.profile));
    const version = obj['catalog_version'];
    const reader = typeof version === 'string' && Object.hasOwn(this.readers, version)
      ? this.readers[version]! : this.fallback;
    return reader.validate(raw, topic, expectedRun);
  }
}
