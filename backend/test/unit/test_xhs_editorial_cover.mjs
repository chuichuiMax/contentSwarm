import assert from 'node:assert/strict';
import { createRequire, registerHooks } from 'node:module';
import test from 'node:test';

registerHooks({
  resolve(specifier, context, nextResolve) {
    return nextResolve(specifier === '@/lib/coverElements' ? 'file:///app/frontend/src/lib/coverElements.ts' : specifier, context);
  },
});

const { buildXhsEditorialCover, readXhsEditorialContent, reflowXhsEditorialNodes, xhsEditorialDefault } = await import('/app/frontend/src/lib/xhsEditorialCover.ts');
const { DesignFileSchema } = createRequire('/app/package.json')('@hc/schema');
const measure = (text, size) => [...text].reduce((sum, char) => sum + size * (/^[\x00-\x7F]$/.test(char) ? 0.55 : 1), 0);
const slot = (result, id) => result.file.pages[0].children.find((node) => node.data?.slotId === id);

test('editorial cover has the specified default line boxes and editable bindings', () => {
  const result = buildXhsEditorialCover(xhsEditorialDefault, measure);
  assert.equal(result.success, true);
  DesignFileSchema.parse(result.file);
  assert.deepEqual([slot(result, 'title').transform.x, slot(result, 'title').transform.y], [96, 272]);
  assert.equal(slot(result, 'subtitle').transform.y, 608);
  assert.equal(slot(result, 'numberMain').transform.y, 720);
  assert.equal(slot(result, 'footnote').transform.y, 1304);
  assert.equal(slot(result, 'tagPrimary').transform.x, 96);
  assert.deepEqual(slot(result, 'numberMain').children.map((child) => child.data.slotId), ['numberValue', 'numberUnit']);
  assert.deepEqual(readXhsEditorialContent(result.file.pages[0].children), xhsEditorialDefault);
  const restored = JSON.parse(JSON.stringify(result.file));
  assert.deepEqual(readXhsEditorialContent(restored.pages[0].children), xhsEditorialDefault);
  assert.equal(restored.pages[0].children.find((node) => node.data?.slotId === 'title').data.layoutTemplateId, 'xhs_left_editorial_v1');
});

test('one-line title and two-line subtitle flow without overlap', () => {
  const one = buildXhsEditorialCover({ ...xhsEditorialDefault, title: '旧房改造' }, measure);
  const two = buildXhsEditorialCover({ ...xhsEditorialDefault, subtitle: '拆清项目\n看懂人工费用' }, measure);
  assert.equal(one.success && two.success, true);
  assert.equal(slot(one, 'subtitle').transform.y, 464);
  assert.equal(slot(two, 'numberMain').transform.y, 776);
});

test('optional slots disappear and zero remains a number', () => {
  const empty = buildXhsEditorialCover({ title: '旧房改造', subtitle: '', tags: { primary: '', secondary: '' }, number: { value: '', unit: '元' }, footnote: '' }, measure);
  assert.equal(empty.success, true);
  assert.deepEqual(empty.file.pages[0].children.map((node) => node.data.slotId), ['title']);
  const zero = buildXhsEditorialCover({ ...xhsEditorialDefault, number: { value: '0', unit: '元' } }, measure);
  assert.equal(slot(zero, 'numberMain').type, 'group');
  assert.equal(slot(zero, 'separator').type, 'shape');
});

test('long numeric row stacks tag and overflow returns a slot error', () => {
  const stacked = buildXhsEditorialCover({ ...xhsEditorialDefault, number: { value: '123456789012345', unit: '万元' } }, measure);
  assert.equal(stacked.success, true);
  assert.equal(slot(stacked, 'separator'), undefined);
  assert.equal(slot(stacked, 'tagSecondary').transform.y, 856);
  const tooLong = buildXhsEditorialCover({ ...xhsEditorialDefault, title: '装修'.repeat(50) }, measure);
  assert.equal(tooLong.success, false);
  assert.deepEqual([tooLong.errors[0].slotId, tooLong.errors[0].code], ['title', 'TEXT_OVERFLOW']);
  const missing = buildXhsEditorialCover({ ...xhsEditorialDefault, title: '   ' }, measure);
  assert.equal(missing.success, false);
  assert.deepEqual([missing.errors[0].slotId, missing.errors[0].code], ['title', 'REQUIRED']);
});

test('reflow keeps existing object ids and reacts to edited text', () => {
  const initial = buildXhsEditorialCover(xhsEditorialDefault, measure);
  assert.equal(initial.success, true);
  const nodes = structuredClone(initial.file.pages[0].children);
  const title = nodes.find((node) => node.data?.slotId === 'title');
  const subtitle = nodes.find((node) => node.data?.slotId === 'subtitle');
  title.content = [{ ...title.content[0], runs: [{ ...title.content[0].runs[0], text: '旧房改造' }] }];
  const next = reflowXhsEditorialNodes(nodes, measure);
  assert.equal(next.success, true);
  assert.equal(next.nodes.find((node) => node.data?.slotId === 'title').id, title.id);
  assert.equal(next.nodes.find((node) => node.data?.slotId === 'subtitle').id, subtitle.id);
  assert.equal(next.nodes.find((node) => node.data?.slotId === 'subtitle').transform.y, 464);
});

test('decimal number and long tag stay editable without text truncation', () => {
  const result = buildXhsEditorialCover({ ...xhsEditorialDefault, tags: { ...xhsEditorialDefault.tags, secondary: '三室两厅大阳台' }, number: { value: '3.3', unit: '万元' } }, measure);
  assert.equal(result.success, true);
  assert.equal(readXhsEditorialContent(result.file.pages[0].children).number.value, '3.3');
  assert.equal(slot(result, 'numberMain').children[1].content[0].runs[0].text, '万元');
});
