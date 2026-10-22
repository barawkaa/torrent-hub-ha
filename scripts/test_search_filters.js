/* Offline browser-free test of the observed-result filters. */
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const box={innerHTML:'',classList:{add(){},remove(){}},addEventListener(){}};
const ctx={window:{},Date:Date};
vm.runInNewContext(fs.readFileSync('torrent-hub/app/static/filters.js','utf8'),ctx);
const F=ctx.window.TorrentFilters;
const f=new F(box,function(){});
const items=[
  {title:'Отец / The Father (2020) BDRip 1080p Дубляж LostFilm',size:9*1073741824,seeders:40},
  {title:'Отец солдата (1964) Blu-ray 720p',size:5*1073741824,seeders:12},
  {title:'Отец невесты (1991) WEB-DL 2160p',size:50*1073741824,seeders:50}
];
f.setResults(items);
assert.equal(f.facets[0].year,'2020');
assert.equal(f.facets[1].year,'1964');
assert.notEqual(f.facets[0].film,f.facets[1].film);
assert.equal(f.facets[0].studio,'LostFilm');
assert.equal(f.facets[0].source,'BDRip');
f.selected.year='2020';
assert.equal(f.filter(items).length,1);
f.selected={};
f.minSize='7';f.maxSize='150';
assert.equal(f.filter(items).length,2);
console.log('PASS observed metadata, film disambiguation and size filters');
