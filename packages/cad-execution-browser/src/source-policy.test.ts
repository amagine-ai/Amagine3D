import { describe, expect, it } from 'vitest';

import { safeWorkspaceSegment, validateCadSource } from './source-policy';

describe('CAD source policy', () => {
  it('accepts the allowlisted single-color helper profile', () => {
    expect(() =>
      validateCadSource(
        'from build123d import Box\nfrom amagine_cad import publish_model\nbody = Box(1, 2, 3)\npublish_model(body, "box")',
        'single-color',
      ),
    ).not.toThrow();
    expect(() =>
      validateCadSource(
        `from amagine_cad import (
    observe_feature,
    publish_model,
    subtract_checked,
)
publish_model(None, "box")`,
        'single-color',
      ),
    ).not.toThrow();
    expect(() =>
      validateCadSource(
        'from amagine_cad import publish_model\npublish_model(None, "box", out_dir=r"cad_out")',
        'single-color',
      ),
    ).not.toThrow();
  });

  it.each(['js', 'micropip', 'socket', 'subprocess', 'pathlib', 'pyodide'])(
    'rejects the forbidden %s import',
    (moduleName) => {
      expect(() =>
        validateCadSource(`import ${moduleName}`, 'single-color'),
      ).toThrow(/not allowed|forbidden/iu);
    },
  );

  it('rejects cross-profile helper calls in both directions', () => {
    expect(() =>
      validateCadSource(
        'from amagine_cad import publish_color_model\npublish_color_model({}, "bad")',
        'single-color',
      ),
    ).toThrow(/profile boundary/u);
    expect(() =>
      validateCadSource(
        `from amagine_cad import (
    publish_color_model,
    subtract_checked,
)`,
        'single-color',
      ),
    ).toThrow(/profile boundary/u);
    expect(() =>
      validateCadSource(
        'from amagine_cad import publish_model\npublish_model(None, "bad")',
        'multi-color',
      ),
    ).toThrow(/profile boundary/u);
  });

  it('reports the build123d Cylinder height keyword before execution', () => {
    expect(() =>
      validateCadSource(
        'from build123d import Cylinder\nbody = Cylinder(radius=4, depth=8)',
        'single-color',
      ),
    ).toThrow(/Cylinder uses 'height='/u);
  });

  it('reports unsupported Scale and incomplete finishing helpers before execution', () => {
    expect(() =>
      validateCadSource(
        'from build123d import *\nbody = Scale(2, 1, 1) * body',
        'single-color',
      ),
    ).toThrow(/has no Scale transform/u);
    expect(() =>
      validateCadSource(
        'round_edges_checked(body, body.edges())',
        'single-color',
      ),
    ).toThrow(/requires 'radius'/u);
    expect(() =>
      validateCadSource(
        `round_edges_checked(
  body,
  lambda current: current.edges().filter_by(Axis.Z),
  radius=EDGE_RADIUS,
  label="vertical",
)`,
        'single-color',
      ),
    ).not.toThrow();
    expect(() =>
      validateCadSource(
        'bevel_edges_checked(body, body.edges())',
        'single-color',
      ),
    ).toThrow(/requires 'length'/u);
  });

  it.each([
    ['Cylinder(radius=2, h=5)', /uses 'height=', not 'h='/u],
    [
      'extrude(Rectangle(3, 2), direction=(0, 0, 1))',
      /uses 'dir=', not 'direction='/u,
    ],
    [
      'revolve(Rectangle(3, 2), angle=90)',
      /uses 'revolution_arc=', not 'angle='/u,
    ],
    ['RegularPolygon(radius=3, sides=6)', /uses 'side_count=', not 'sides='/u],
    ['Ellipse(width=3, height=2)', /uses 'x_radius=' and 'y_radius='/u],
    ['SlotCenterLine(10, 4)', /has no SlotCenterLine/u],
    ['edge.center().z', /coordinates are uppercase/u],
    ['Workplane("XY")', /CadQuery Workplane syntax/u],
  ])('reports the stale or foreign API spelling in %s', (source, message) => {
    expect(() => validateCadSource(source, 'single-color')).toThrow(message);
  });

  it('rejects file access, dunder traversal, and unsafe project paths', () => {
    expect(() =>
      validateCadSource('open("/etc/passwd")', 'single-color'),
    ).toThrow(/open/u);
    expect(() => validateCadSource('value.__class__', 'single-color')).toThrow(
      /Dunder/u,
    );
    expect(() => safeWorkspaceSegment('../another-project')).toThrow(/Unsafe/u);
    expect(() =>
      validateCadSource('from amagine_cad import Path', 'single-color'),
    ).toThrow(/profile boundary/u);
    expect(() =>
      validateCadSource(
        'from amagine_cad import publish_model as write_anywhere',
        'single-color',
      ),
    ).toThrow(/aliased/u);
    expect(() =>
      validateCadSource(
        'from amagine_cad import publish_model\npublish_model(None, "x", out_dir="/tmp")',
        'single-color',
      ),
    ).toThrow(/cad_out/u);
    expect(() =>
      validateCadSource(
        'from amagine_cad import publish_model\npublish_model(None, "x", out_dir=r"/tmp")',
        'single-color',
      ),
    ).toThrow(/cad_out/u);
  });
});
