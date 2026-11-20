const fs = require('node:fs');
const path = require('node:path');
const { Contract, Rejected } = require('../../packages/contracts-ts/.build/index.js');
const root = path.resolve(__dirname, '../..');
const [casesFile, outputFile] = process.argv.slice(2);
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const profile = read(path.join(root, 'contracts/profile.json'));
const schemas = Object.fromEntries(Object.entries(profile.schemas).map(([version, name]) =>
  [version, read(path.join(root, 'contracts', name))]));
const catalogSchema = read(path.join(root, 'contracts', profile.catalog_schema));
const portableSchema = read(path.join(root, 'contracts', profile.portable_schema));
const defaultCatalog = read(path.join(root, 'catalog/reference-signals.json'));
const results = [];
for (const entry of read(casesFile)) {
  const result = { id: entry.id, accepted: false, reason: null };
  let validator;
  const catalog = entry.catalog === undefined ? structuredClone(defaultCatalog) : structuredClone(entry.catalog);
  try { validator = new Contract({ profile, schemas, portableSchema, catalogSchema, catalog }); }
  catch (error) {
    if (!String(error).includes('Invalid catalog configuration')) throw error;
    result.reason = 'configuration_error'; results.push(result); continue;
  }
  if (entry.mutate_catalog_after) catalog.signals['battery.pack.soc'].unit = 'changed-after-construction';
  const raw = Buffer.from(entry.raw_base64, 'base64');
  try { validator.validate(raw, entry.topic, entry.expected_run); result.accepted = true; }
  catch (error) { if (!(error instanceof Rejected)) throw error; result.reason = error.reason; }
  if (entry.downgrade) {
    const encoded = Buffer.from(validator.forLegacy(raw, entry.topic, entry.expected_run));
    result.downgrade = JSON.parse(encoded.toString('utf8'));
    result.downgrade_raw_base64 = encoded.toString('base64');
  }
  results.push(result);
}
fs.writeFileSync(outputFile, JSON.stringify(results, null, 2) + '\n');
