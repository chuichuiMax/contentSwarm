// Compile the editable System Materials cover layouts into the HyCanvas API
// catalog consumed by ContentSwarm. Run inside hycanvas-app with Node 24+.
import { readFileSync, writeFileSync } from 'node:fs';
import { createRequire, registerHooks } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const coverPath = join(root, 'frontend/src/lib/coverElements.ts');
const output = join(root, 'backend/internal/templates/system_cover_seed.json');
registerHooks({
  resolve(specifier, context, nextResolve) {
    return nextResolve(specifier === '@/lib/coverElements' ? pathToFileURL(coverPath).href : specifier, context);
  },
});
const { DesignFileSchema } = createRequire(join(root, 'frontend/package.json'))('@hc/schema');
const { buildBeforeAfterCover, buildCoverLayout, coverLayouts, coverStyleTemplates } = await import(pathToFileURL(coverPath).href);
const { buildXhsEditorialCover, xhsEditorialDefault } = await import(pathToFileURL(join(root, 'frontend/src/lib/xhsEditorialCover.ts')).href);

const previousCrypto = Object.getOwnPropertyDescriptor(globalThis, 'crypto');
let sequence = 0;
Object.defineProperty(globalThis, 'crypto', { configurable: true, value: { randomUUID: () => `system-cover-node-${++sequence}` } });
const measure = (text, size) => [...text].reduce((sum, char) => sum + size * (/^[\x00-\x7F]$/.test(char) ? 0.55 : 1), 0);
const editorial = buildXhsEditorialCover(xhsEditorialDefault, measure);
if (!editorial.success) throw new Error(`Editorial cover failed: ${editorial.errors[0].message}`);
const source = [
  ...coverStyleTemplates.map((style) => ({ id: style.id, title: style.name, file: buildCoverLayout('quote', undefined, 0.95, undefined, 'photo-center', style.id) })),
  ...coverLayouts.map((layout) => ({ id: layout.id, title: layout.name, file: buildCoverLayout(layout.id) })),
  { id: 'before-after', title: '装修前后对比', file: buildBeforeAfterCover() },
  { id: 'editorial', title: '左对齐大标题', file: editorial.file },
];
if (previousCrypto) Object.defineProperty(globalThis, 'crypto', previousCrypto);

const entries = source.map(({ id, title, file }) => {
  file.title = file.pages[0].name = title;
  file.meta.templateZone = 'xiaohongshu';
  DesignFileSchema.parse(file);
  const nodes = [];
  const visit = (node) => { nodes.push(node); node.children?.forEach(visit); };
  file.pages[0].children.forEach(visit);
  const titles = new Set(coverStyleTemplates.filter((item) => item.category === '标题').map((item) => item.id));
  const subtitles = new Set(coverStyleTemplates.filter((item) => item.category === '副标题').map((item) => item.id));
  const titleNode = nodes.find((node) => node.type === 'text' && (node.data?.slotId === 'title' || node.name.includes('主标题') || titles.has(node.data?.coverElementId) || ['two-line', 'headline', 'big-title'].includes(node.data?.coverElementId)));
  const subtitle = nodes.find((node) => node.type === 'text' && node !== titleNode && (node.data?.slotId === 'subtitle' || node.name.includes('副标题') || subtitles.has(node.data?.coverElementId)));
  const fillableFields = [
    ...(titleNode ? [{ nodeId: titleNode.id, kind: 'text', label: '主标题', semanticRole: 'title', constraints: { maxChars: 48 } }] : []),
    ...(subtitle ? [{ nodeId: subtitle.id, kind: 'text', label: '副标题', semanticRole: 'subtitle', constraints: { maxChars: 60 } }] : []),
  ];
  if (id === 'before-after') {
    for (const node of nodes.filter((item) => item.type === 'shape' && item.name.endsWith('照片占位'))) {
      fillableFields.push({ nodeId: node.id, kind: 'image', label: node.name, semanticRole: 'image' });
    }
  }
  const templateId = `system-cover-${id}`;
  return { template: {
    id: templateId, title, visibility: 'public', ownerId: 'hycanvas', workspaceId: null,
    categories: ['social', '小红书'], tags: ['小红书', '系统素材'],
    style: { palette: [], typography: [], styleTags: [] },
    format: { width: 1080, height: 1440, unit: 'px' }, pageCount: 1,
    previewUrls: [], designFileKey: `seed:${templateId}`, fillableFields,
    attributions: [], version: 1, createdAt: '2026-09-17T00:00:00.000Z', updatedAt: '2026-09-17T00:00:00.000Z',
  }, file };
});
if (entries.length !== coverStyleTemplates.length + coverLayouts.length + 2) throw new Error('System cover catalog count mismatch');
const serialized = JSON.stringify(entries) + '\n';
if (process.argv.includes('--check')) {
  if (readFileSync(output, 'utf8') !== serialized) throw new Error('System cover template catalog is stale');
  console.log(`ok: ${entries.length} system cover templates`);
} else {
  writeFileSync(output, serialized);
  console.log(`wrote ${entries.length} system cover templates`);
}
