import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve, basename } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import ts from "typescript";

test("recorded demo reuses dashboard presenters without operational hooks or preview fixtures", () => {
  const visited = new Set<string>();
  function visit(file: string) {
    if (visited.has(file)) return;
    visited.add(file);
    assert.ok(
      !/^(useRouteEnergy|useChargingAdvisory|useConnection|fleetPolling|Operations|RouteEnergyForecasts|main|replayMain)\./.test(
        basename(file),
      ),
      `Operational driver entered the demo: ${file}`,
    );
    assert.ok(
      !/[\\/]preview[\\/]/.test(file),
      `Preview fixture entered the demo: ${file}`,
    );
    const source = ts.createSourceFile(
      file,
      readFileSync(file, "utf8"),
      ts.ScriptTarget.Latest,
      true,
    );
    function follow(value: string) {
      if (value.startsWith(".") && /\.tsx?$/.test(value))
        visit(resolve(dirname(file), value));
    }
    function inspect(node: ts.Node) {
      if (
        ts.isImportDeclaration(node) &&
        ts.isStringLiteral(node.moduleSpecifier)
      ) {
        const clause = node.importClause,
          bindings = clause?.namedBindings;
        const onlyTypes =
          clause?.isTypeOnly ||
          (!clause?.name &&
            bindings &&
            ts.isNamedImports(bindings) &&
            bindings.elements.length > 0 &&
            bindings.elements.every((e) => e.isTypeOnly));
        if (!onlyTypes) follow(node.moduleSpecifier.text);
      }
      if (
        ts.isExportDeclaration(node) &&
        !node.isTypeOnly &&
        node.moduleSpecifier &&
        ts.isStringLiteral(node.moduleSpecifier)
      )
        follow(node.moduleSpecifier.text);
      if (
        ts.isCallExpression(node) &&
        node.expression.kind === ts.SyntaxKind.ImportKeyword &&
        node.arguments[0] &&
        ts.isStringLiteral(node.arguments[0])
      )
        follow(node.arguments[0].text);
      ts.forEachChild(node, inspect);
    }
    inspect(source);
  }
  visit(
    fileURLToPath(
      new URL("../src/features/demo/demoMain.tsx", import.meta.url),
    ),
  );
  for (const component of [
    "DashboardShell.tsx",
    "Measurements.tsx",
    "SummaryMetrics.tsx",
    "RouteEnergyDetails.tsx",
    "FleetVehicle.tsx",
    "useMapScene.ts",
  ])
    assert.ok(
      [...visited].some((file) => basename(file) === component),
      `Missing shared presenter: ${component}`,
    );
});
