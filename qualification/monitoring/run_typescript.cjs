const fs = require('node:fs');
const path = require('node:path');
const { ContractRegistry, Rejected } = require('../../packages/contracts-ts/.build/index.js');
const root = path.resolve(__dirname, '../..');
const read = name => JSON.parse(fs.readFileSync(name, 'utf8'));
const profile = read(path.join(root, 'contracts/monitoring.profile.json'));
const bundle = {
  profile,
  schemas: Object.fromEntries(Object.entries(profile.schemas).map(([v,n]) => [v,read(path.join(root,'contracts',n))])),
  portableSchema: read(path.join(root,'contracts',profile.portable_schema)),
  catalogSchema: read(path.join(root,'contracts',profile.catalog_schema)),
  catalogSchemas: Object.fromEntries(Object.entries(profile.catalog_schemas).map(([v,n])=>[v,read(path.join(root,'contracts',n))])),
  catalog: read(path.join(root,'catalog/monitoring-signals.json')),
};
const results = read(process.argv[2]).map(entry => {
  const catalogs = entry.catalog ? [entry.catalog] : Object.values(profile.catalogs).map(n=>read(path.join(root,n)));
  const primary=entry.catalog??bundle.catalog;
  const contract = new ContractRegistry({...bundle,catalogSchema:bundle.catalogSchemas[primary.catalog_version]??bundle.catalogSchema,catalog:primary},catalogs);
  try { contract.validate(new TextEncoder().encode(JSON.stringify(entry.body)),entry.topic,entry.run);
    return {accepted:true,reason:null}; }
  catch(error) { if(!(error instanceof Rejected)) throw error;
    return {accepted:false,reason:error.reason}; }
});
fs.writeFileSync(process.argv[3],JSON.stringify(results));
