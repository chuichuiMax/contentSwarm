import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
const require = createRequire('/app/frontend/package.json');
const { createNode, TextNodeSchema } = require('@hc/schema');
const { coverElements, coverElementText } = await import('/app/frontend/src/lib/coverElements.ts');

test('all cover presets are schema-valid editable text and fit cover sizes', () => {
  assert.equal(coverElements.length, 54);
  for (const page of [{ width: 1080, height: 1440 }, { width: 360, height: 480 }]) {
    for (const preset of coverElements) {
      const node = createNode('text', coverElementText(preset, page));
      TextNodeSchema.parse(node);
      assert.ok(node.size.width <= page.width);
      assert.ok(node.size.height <= page.height);
      assert.ok(node.content.every(p => p.runs[0].text.length > 0));
      const saved = JSON.parse(JSON.stringify(node));
      saved.content[0].runs[0].text = '用户修改';
      TextNodeSchema.parse(saved);
      assert.equal(saved.content[0].runs[0].text, '用户修改');
    }
  }
});

test('white headline and subtitle presets retain editable fill and thick colored outlines', () => {
  const presets = coverElements.filter(p => p.outlineColor);
  assert.equal(presets.length, 12);
  assert.equal(new Set(presets.map(p => p.outlineColor)).size, 6);
  for (const preset of presets) {
    const node = createNode('text', coverElementText(preset, { width: 1080, height: 1440 }));
    TextNodeSchema.parse(node);
    assert.equal(node.content[0].runs[0].style.fill.color.srgb.r, 1);
    assert.equal(node.content[0].runs[0].style.fill.color.srgb.g, 1);
    assert.equal(node.content[0].runs[0].style.fill.color.srgb.b, 1);
    assert.equal(node.textEffects[0].kind, 'outline');
    assert.equal(node.textEffects[0].width, preset.category === '标题' ? 12 : 10);
    assert.equal(node.textEffects[0].join, 'round');
    node.content[0].runs[0].text = '可编辑文字';
    TextNodeSchema.parse(node);
  }
});

test('number and unit share a text box with separately editable styles', () => {
  for (const preset of coverElements.filter(p => p.category === '数字')) {
    const a = coverElementText(preset, { width: 1080, height: 1440 });
    const b = coverElementText(preset, { width: 1080, height: 1440 });
    assert.equal(a.content[0].runs.length, 2);
    assert.ok(a.content[0].runs[0].style.fontSize > a.content[0].runs[1].style.fontSize);
    a.content[0].runs[0].text = '999';
    assert.equal(b.content[0].runs[0].text, preset.lines[0]);
  }
});

test('automatic layouts keep editable elements inside 3:4 pages without overlapping slots', async () => {
  const { coverLayouts, buildCoverLayout } = await import('/app/frontend/src/lib/coverElements.ts');
  for (const layout of coverLayouts) {
    const file = buildCoverLayout(layout.id);
    const page = file.pages[0];
    assert.equal(page.width, 1080);
    assert.equal(page.height, 1440);
    assert.ok(page.children.length >= 6);
    for (const [index, node] of page.children.entries()) {
      TextNodeSchema.parse(node);
      const { x, y } = node.transform;
      assert.ok(x >= 60 && y >= 60);
      assert.ok(x + node.size.width <= 1020);
      assert.ok(y + node.size.height <= 1390);
      for (const other of page.children.slice(index + 1)) {
        const separated = x + node.size.width <= other.transform.x ||
          other.transform.x + other.size.width <= x ||
          y + node.size.height <= other.transform.y ||
          other.transform.y + other.size.height <= y;
        assert.ok(separated, `${layout.id}: ${node.name} overlaps ${other.name}`);
      }
    }
    const again = buildCoverLayout(layout.id);
    assert.notEqual(again.pages[0].children[0].id, page.children[0].id);
  }
});

test('photo layouts keep the central image clear and compact editable text inside information bands', async () => {
  const { buildCoverLayout, coverLayouts } = await import('/app/frontend/src/lib/coverElements.ts');
  const { ImageNodeSchema, ShapeNodeSchema } = require('@hc/schema');
  const source = { id: 'material-test', name: '装修案例', url: 'data:image/png;base64,example', mime: 'image/png', width: 1600, height: 900 };
  for (const layout of coverLayouts) {
    const file = JSON.parse(JSON.stringify(buildCoverLayout(layout.id, source, 0.95)));
    const [photo, top, bottom, ...texts] = file.pages[0].children;
    ImageNodeSchema.parse(photo);
    assert.equal(photo.fit, 'cover');
    assert.equal(photo.source.naturalWidth, 1600);
    assert.equal(file.assets.find(asset => asset.id === photo.source.assetId).url, source.url);
    assert.equal(photo.data.provenance.materialItemId, source.id);
    for (const mask of [top, bottom]) {
      ShapeNodeSchema.parse(mask);
      assert.equal(mask.opacity, 0.95);
      assert.equal(mask.size.height, 320);
    }
    assert.equal(top.transform.y, 0);
    assert.equal(bottom.transform.y, 1120);
    for (const [index, node] of texts.entries()) {
      TextNodeSchema.parse(node);
      const { x, y } = node.transform;
      assert.ok(x >= 64 && x + node.size.width <= 1016);
      assert.ok(y >= 0 && y + node.size.height <= 1440);
      assert.ok(y + node.size.height <= 320 || y >= 1120, `${layout.id}: ${node.name} covers the photo`);
      assert.ok(node.content[0].runs[0].style.fontSize <= 80);
      for (const other of texts.slice(index + 1)) {
        assert.ok(x + node.size.width <= other.transform.x || other.transform.x + other.size.width <= x ||
          y + node.size.height <= other.transform.y || other.transform.y + other.size.height <= y,
          `${layout.id}: ${node.name} overlaps ${other.name}`);
      }
    }
    assert.equal(buildCoverLayout(layout.id).assets.length, 0);
  }
});

test('all four compositions preserve editable content, direction and collision-free photo space', async () => {
  const { buildCoverLayout, coverLayouts, coverCompositions } = await import('/app/frontend/src/lib/coverElements.ts');
  const source = { id: 'photo', name: '案例', url: 'data:image/png;base64,example', mime: 'image/png', width: 1080, height: 1440 };
  assert.equal(coverCompositions.length, 4);
  for (const background of [undefined, source]) for (const layout of coverLayouts) for (const composition of coverCompositions) {
    const file = JSON.parse(JSON.stringify(buildCoverLayout(layout.id, background, 0.95, composition.id)));
    const texts = file.pages[0].children.filter(node => node.type === 'text');
    const original = buildCoverLayout(layout.id, background).pages[0].children.filter(node => node.type === 'text');
    assert.deepEqual(texts.flatMap(node => node.content.flatMap(p => p.runs.map(r => r.text))).sort(),
      original.flatMap(node => node.content.flatMap(p => p.runs.map(r => r.text))).sort());
    for (const [index, node] of texts.entries()) {
      TextNodeSchema.parse(node);
      const { x, y } = node.transform;
      assert.ok(x >= 0 && y >= 0 && x + node.size.width <= 1080 && y + node.size.height <= 1440);
      if (background) assert.ok(y + node.size.height <= 320 || y >= 1120);
      for (const other of texts.slice(index + 1)) assert.ok(
        x + node.size.width <= other.transform.x || other.transform.x + other.size.width <= x ||
        y + node.size.height <= other.transform.y || other.transform.y + other.size.height <= y,
        `${layout.id}/${composition.id}: ${node.name} overlaps ${other.name}`);
    }
    const number = texts.find(node => ['price', 'area', 'number'].includes(node.data.coverElementId));
    const labels = texts.filter(node => ['layout', 'service', ...(layout.id === 'quote' ? ['trade'] : [])].includes(node.data.coverElementId));
    const separator = file.pages[0].children.find(node => node.name === '数字与标签分隔线');
    if (composition.id === 'number-left' || composition.id === 'number-right') {
      assert.ok(separator, `${layout.id}/${composition.id}: separator is visible`);
      assert.equal(separator.type, 'shape');
      assert.equal(separator.size.height, 76);
      const [left, right] = composition.id === 'number-left' ? [number, labels[0]] : [labels[0], number];
      assert.equal(separator.transform.x - (left.transform.x + left.size.width), 24);
      assert.equal(right.transform.x - (separator.transform.x + separator.size.width), 24);
      const centers = [number, labels[0], separator].map(node => node.transform.y + node.size.height / 2);
      assert.ok(Math.max(...centers) - Math.min(...centers) <= 5, `${layout.id}/${composition.id}: summary centers align`);
    } else assert.equal(separator, undefined);
    for (const label of labels) {
      if (composition.id === 'number-left') assert.ok(number.transform.x + number.size.width <= label.transform.x);
      if (composition.id === 'number-right') assert.ok(label.transform.x + label.size.width <= number.transform.x);
      if (composition.id === 'number-top') assert.ok(number.transform.y + number.size.height <= label.transform.y);
      if (composition.id === 'number-bottom') assert.ok(label.transform.y + label.size.height <= number.transform.y);
    }
  }
});

test('title compositions combine with every summary layout without obscuring the photo or overlapping text', async () => {
  const { buildCoverLayout, coverLayouts, coverCompositions, coverTitleCompositions } = await import('/app/frontend/src/lib/coverElements.ts');
  const source = { id: 'photo', name: '案例', url: 'data:image/png;base64,example', mime: 'image/png', width: 1080, height: 1440 };
  for (const background of [undefined, source]) for (const layout of coverLayouts) for (const summary of coverCompositions) for (const title of coverTitleCompositions) {
    const file = JSON.parse(JSON.stringify(buildCoverLayout(layout.id, background, 0.95, summary.id, title.id)));
    const texts = file.pages[0].children.filter(node => node.type === 'text');
    for (const [index, node] of texts.entries()) {
      TextNodeSchema.parse(node);
      const { x, y } = node.transform;
      assert.ok(x >= 0 && y >= 0 && x + node.size.width <= 1080 && y + node.size.height <= 1440);
      if (background) assert.ok(y + node.size.height <= 320 || y >= 1120);
      for (const other of texts.slice(index + 1)) assert.ok(
        x + node.size.width <= other.transform.x || other.transform.x + other.size.width <= x ||
        y + node.size.height <= other.transform.y || other.transform.y + other.size.height <= y,
        `${layout.id}/${summary.id}/${title.id}: ${node.name} overlaps ${other.name}`);
    }
    const numbers = texts.filter(node => ['price', 'area', 'number'].includes(node.data.coverElementId));
    const bottomLabels = texts.filter(node => ['city', 'layout', 'trade', 'service'].includes(node.data.coverElementId) && node.transform.y >= 320);
    assert.equal(numbers.length, 1, `${layout.id}: exactly one number`);
    assert.equal(bottomLabels.length, 1, `${layout.id}: exactly one bottom label`);
    const main = texts.find(node => ['headline', 'two-line'].includes(node.data.coverElementId));
    const sub = texts.find(node => node.data.coverElementId === 'description');
    if (title.id === 'subtitle-first') assert.ok(sub.transform.y + sub.size.height <= main.transform.y);
    else if (title.id === 'side-by-side') assert.ok(main.transform.x + main.size.width <= sub.transform.x);
    else assert.ok(main.transform.y + main.size.height <= sub.transform.y);
    if (title.id === 'centered') assert.ok([main, sub].every(node => node.content.every(p => p.style.align === 'center')));
  }
});

test('automatic layouts allow either title or summary composition alone', async () => {
  const { buildCoverLayout, coverLayouts } = await import('/app/frontend/src/lib/coverElements.ts');
  const source = { id: 'photo', name: '案例', url: 'data:image/png;base64,example', mime: 'image/png', width: 1080, height: 1440 };
  for (const layout of coverLayouts) for (const background of [undefined, source]) {
    const titleOnly = buildCoverLayout(layout.id, background, 0.95, undefined, 'centered');
    const summaryOnly = buildCoverLayout(layout.id, background, 0.95, 'number-right');
    assert.ok(titleOnly.title.includes('居中组合'));
    assert.ok(!titleOnly.title.includes('左标签 · 右数字'));
    assert.ok(summaryOnly.title.includes('左标签 · 右数字'));
    assert.ok(!summaryOnly.title.includes('居中组合'));
    assert.equal(titleOnly.pages[0].children.filter(node => node.name === '数字与标签分隔线').length, 0);
    assert.equal(summaryOnly.pages[0].children.filter(node => node.name === '数字与标签分隔线').length, 1);
    for (const file of [titleOnly, summaryOnly]) {
      const texts = file.pages[0].children.filter(node => node.type === 'text');
      assert.equal(texts.filter(node => ['price', 'area', 'number'].includes(node.data.coverElementId)).length, 1);
      assert.equal(texts.filter(node => ['headline', 'two-line'].includes(node.data.coverElementId)).length, 1);
      for (const node of texts) TextNodeSchema.parse(node);
    }
  }
});

test('photo-centered title modes place editable text on the image without masks', async () => {
  const { buildCoverLayout, coverLayouts, coverCompositions, coverTitleCompositions } = await import('/app/frontend/src/lib/coverElements.ts');
  const source = { id: 'photo', name: '案例', url: 'data:image/png;base64,example', mime: 'image/png', width: 1080, height: 1440 };
  for (const layout of coverLayouts) for (const summary of coverCompositions) for (const title of coverTitleCompositions) {
    const file = JSON.parse(JSON.stringify(buildCoverLayout(layout.id, source, 0.95, summary.id, title.id, 'photo-center')));
    const nodes = file.pages[0].children;
    assert.equal(nodes[0].type, 'image');
    assert.equal(nodes.filter(node => node.name.includes('信息区底色')).length, 0);
    const texts = nodes.filter(node => node.type === 'text');
    const main = texts.find(node => ['headline', 'two-line'].includes(node.data.coverElementId));
    const sub = texts.find(node => node.data.coverElementId === 'description');
    for (const node of [main, sub]) {
      TextNodeSchema.parse(node);
      assert.ok(node.transform.y >= 320 && node.transform.y + node.size.height <= 1120);
      assert.equal(node.textEffects[0].kind, 'shadow');
    }
    for (const [index, node] of texts.entries()) for (const other of texts.slice(index + 1)) {
      assert.ok(node.transform.x + node.size.width <= other.transform.x || other.transform.x + other.size.width <= node.transform.x ||
        node.transform.y + node.size.height <= other.transform.y || other.transform.y + other.size.height <= node.transform.y,
        `${layout.id}/${summary.id}/${title.id}: ${node.name} overlaps ${other.name}`);
    }
  }
  const withoutPhoto = buildCoverLayout('quote', undefined, 0.95, 'number-left', 'stacked', 'photo-center');
  assert.equal(withoutPhoto.pages[0].children.find(node => node.data?.coverElementId === 'two-line').transform.y, 132);
});

test('every title and subtitle style generates an editable cover template', async () => {
  const { coverStyleTemplates, buildCoverLayout } = await import('/app/frontend/src/lib/coverElements.ts');
  const { GroupNodeSchema } = require('@hc/schema');
  const source = { id: 'photo', name: '案例', url: 'data:image/png;base64,example', mime: 'image/png', width: 1080, height: 1440 };
  assert.equal(coverStyleTemplates.length, coverElements.filter(item => item.category === '标题' || item.category === '副标题').length);
  assert.equal(new Set(coverStyleTemplates.map(item => item.id)).size, coverStyleTemplates.length);
  for (const style of coverStyleTemplates) for (const background of [undefined, source]) {
    const file = buildCoverLayout('quote', background, 0.95, undefined, undefined, 'top', style.id);
    const nodes = file.pages[0].children;
    const selected = nodes.find(node => node.data?.coverElementId === style.id);
    assert.ok(selected, `${style.id} is present`);
    if (style.decoration) {
      GroupNodeSchema.parse(selected);
      assert.equal(selected.children[0].type, 'path');
      TextNodeSchema.parse(selected.children[1]);
    } else TextNodeSchema.parse(selected);
    assert.ok(selected.transform.x + selected.size.width <= 1080);
    assert.ok(selected.transform.y + selected.size.height <= (background ? 320 : 1440));
    assert.ok(file.title.includes(style.name));
  }
  for (const style of coverStyleTemplates) for (const background of [undefined, source]) for (const placement of background ? ['top', 'photo-center'] : ['top']) {
    for (const titleMode of [undefined, 'stacked', 'centered', 'subtitle-first', 'side-by-side']) {
      const nodes = buildCoverLayout('quote', background, 0.95, titleMode ? undefined : 'number-left', titleMode, placement, style.id)
        .pages[0].children.filter(node => node.type === 'text' || node.type === 'group');
      for (const [index, node] of nodes.entries()) for (const other of nodes.slice(index + 1)) assert.ok(
        node.transform.x + node.size.width <= other.transform.x || other.transform.x + other.size.width <= node.transform.x ||
        node.transform.y + node.size.height <= other.transform.y || other.transform.y + other.size.height <= node.transform.y,
        `${style.id}/${placement}/${titleMode}: ${node.name} overlaps ${other.name}`);
    }
  }
});

test('two-photo construction cover keeps photos, brush and outlined words independently editable', async () => {
  const { buildBeforeAfterCover } = await import('/app/frontend/src/lib/coverElements.ts');
  const { DesignFileSchema, ImageNodeSchema, PathNodeSchema } = require('@hc/schema');
  const top = { id: 'living', name: '客厅', url: 'data:image/png;base64,top', mime: 'image/png', width: 1600, height: 900 };
  const bottom = { id: 'work', name: '施工', url: 'data:image/png;base64,bottom', mime: 'image/png', width: 1200, height: 1600 };
  const file = buildBeforeAfterCover(top, bottom);
  DesignFileSchema.parse(file);
  assert.equal(file.assets.length, 2);
  const [upper, lower, brush, main, sub] = file.pages[0].children;
  for (const [index, photo] of [upper, lower].entries()) {
    ImageNodeSchema.parse(photo);
    assert.equal(photo.transform.y, index * 720);
    assert.equal(photo.size.height, 720);
  }
  assert.notEqual(upper.source.assetId, lower.source.assetId);
  PathNodeSchema.parse(brush);
  assert.ok(brush.contours.length > 500);
  assert.equal(brush.fills.length, 1);
  for (const text of [main, sub]) {
    TextNodeSchema.parse(text);
    assert.equal(text.textEffects[0].kind, 'outline');
    assert.equal(text.textEffects[0].width, 22);
    text.content[0].runs[0].text = '可编辑';
    TextNodeSchema.parse(text);
  }
});

test('photo-centered subtitle templates retain their intended readable font size', async () => {
  const { coverStyleTemplates, buildCoverLayout } = await import('/app/frontend/src/lib/coverElements.ts');
  const source = { id: 'photo', name: '案例', url: 'data:image/png;base64,example', mime: 'image/png', width: 1080, height: 1440 };
  for (const style of coverStyleTemplates.filter(item => item.category === '副标题')) {
    const page = buildCoverLayout('quote', source, 0.95, undefined, 'stacked', 'photo-center', style.id).pages[0];
    const node = page.children.find(item => item.data?.coverElementId === style.id);
    assert.ok(node, style.id);
    const text = node.type === 'group' ? node.children.find(item => item.type === 'text') : node;
    assert.equal(text.content[0].runs[0].style.fontSize, style.fontSize, style.id);
    assert.ok(node.transform.y >= 320 && node.transform.y + node.size.height <= 1120, style.id);
  }
});

test('colored subtitle presets retain editable text and vector brush fills at both canvas scales', async () => {
  const { buildCoverElement } = await import('/app/frontend/src/lib/coverElements.ts');
  const { GroupNodeSchema, PathNodeSchema } = require('@hc/schema');
  const presets = coverElements.filter(p => p.id.startsWith('subtitle-') && p.decoration !== 'dry-brush');
  assert.equal(presets.length, 20);
  assert.equal(presets.filter(p => p.decoration === 'brush').length, 10);
  assert.equal(presets.filter(p => p.decoration === 'marker').length, 4);
  for (const page of [{ width: 1080, height: 1440 }, { width: 360, height: 480 }]) for (const preset of presets) {
    const node = JSON.parse(JSON.stringify(buildCoverElement(preset, page)));
    assert.ok(node.size.width <= page.width && node.size.height <= page.height);
    if (preset.decoration) {
      GroupNodeSchema.parse(node);
      const [backdrop, text] = node.children;
      PathNodeSchema.parse(backdrop);
      TextNodeSchema.parse(text);
      assert.equal(backdrop.closed, true);
      assert.ok(backdrop.segments.every(p => p.x >= 0 && p.y >= 0 && p.x <= node.size.width && p.y <= node.size.height));
      if (preset.decoration === 'brush') assert.ok(backdrop.contours.length > 800, `${preset.id}: visible granular brush texture`);
      assert.equal(text.textEffects.length, 0);
      assert.equal(text.content[0].runs[0].text, preset.lines[0]);
      text.content[0].runs[0].text = '修改后的副标题';
      backdrop.fills[0].color.srgb.r = 0.5;
      TextNodeSchema.parse(text);
      PathNodeSchema.parse(backdrop);
      const fresh = buildCoverElement(preset, page);
      assert.notEqual(fresh.id, node.id);
      assert.notEqual(fresh.children[0].id, backdrop.id);
      assert.equal(fresh.children[1].content[0].runs[0].text, preset.lines[0]);
    } else {
      TextNodeSchema.parse(node);
      assert.equal(node.textEffects[0].kind, 'highlight');
    }
  }
});

test('dry brushes preserve deterministic transparent vector grain and one editable backdrop', async () => {
  const { buildCoverElement } = await import('/app/frontend/src/lib/coverElements.ts');
  const { PathNodeSchema } = require('@hc/schema');
  const presets = coverElements.filter(p => p.decoration === 'dry-brush');
  assert.equal(presets.length, 10);
  const profiles = new Set();
  for (const preset of presets) {
    const full = buildCoverElement(preset, { width: 1080, height: 1440 });
    const small = buildCoverElement(preset, { width: 360, height: 480 });
    assert.equal(full.children.length, 2);
    const [brush, text] = full.children;
    PathNodeSchema.parse(JSON.parse(JSON.stringify(brush)));
    TextNodeSchema.parse(text);
    assert.equal(brush.fills.length, 1);
    assert.ok(brush.contours.length > 1000 && brush.contours.length < 1800);
    assert.equal(text.content[0].runs[0].text, preset.lines[0]);
    assert.deepEqual(brush.contours, buildCoverElement(preset, { width: 1080, height: 1440 }).children[0].contours);
    for (const contour of [{ segments: brush.segments }, ...brush.contours]) for (const p of contour.segments) {
      assert.ok(p.x >= 0 && p.x <= brush.size.width && p.y >= 0 && p.y <= brush.size.height);
    }
    assert.ok(Math.abs(small.children[0].segments[0].x * 3 - brush.segments[0].x) < 0.001);
    profiles.add(JSON.stringify([brush.contours.length, brush.segments, brush.contours[500].segments]));
  }
  assert.equal(profiles.size, 10);
});
