import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';
const require = createRequire('/app/frontend/package.json');
const { createNode, TextNodeSchema } = require('@hc/schema');
const { coverElements, coverElementText } = await import('/app/frontend/src/lib/coverElements.ts');

test('all cover presets are schema-valid editable text and fit cover sizes', () => {
  assert.equal(coverElements.length, 12);
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
