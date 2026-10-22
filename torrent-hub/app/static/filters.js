/* Torrent Hub 3.2.0: post-search faceted filtering.
   All choices are sourced from actual result metadata or observed title tokens.
   Compatible with older Android 9 System WebView (no optional chaining). */
(function () {
  'use strict';
  var ALL = '__all__';
  var UNKNOWN = '__unknown__';
  var GB = 1073741824;
  var fields = [
    ['film', 'Название фильма'],
    ['year', 'Год выпуска'],
    ['quality', 'Качество'],
    ['translation', 'Перевод'],
    ['studio', 'Студия озвучки'],
    ['source', 'Источник видео'],
    ['codec', 'Видеокодек'],
    ['hdr', 'HDR / Dolby Vision'],
    ['type', 'Тип контента'],
    ['country', 'Страна']
  ];
  function clean(v) { return String(v == null ? '' : v).trim(); }
  function html(v) { return clean(v).replace(/[&<>"']/g, function (ch) {
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch];
  }); }
  function tokens(title) {
    return clean(title).replace(/[_\.]+/g, ' ');
  }
  function observedYear(item, title) {
    var n = Number(item.release_year || 0);
    if (n >= 1888 && n <= new Date().getFullYear() + 2) return String(n);
    var m = title.match(/(?:^|[\s(\[])((?:19|20)\d{2})(?=[\s)\]])/);
    return m ? m[1] : '';
  }
  function observedFilm(item, title, year) {
    var name = clean(item.name);
    if (name) return name;
    var first = title;
    if (year) {
      var at = first.search(new RegExp('(?:\\(|\\[|\\s)' + year + '(?=[\\s)\\]])'));
      if (at > 1) first = first.slice(0, at);
    }
    first = first.replace(/\s+[\[(](?:WEB[\s-]?DL|Blu[\s-]?ray|BDRip|HDR|1080p|2160p|720p).*$/i, '');
    first = first.replace(/\s+(?:2160p|1080p|720p|4K|WEB[\s-]?DL|Blu[\s-]?ray|BDRip).*$/i, '');
    return first.replace(/[\s./|\-]+$/, '').trim().slice(0, 120) || title.slice(0, 120);
  }
  function observe(item) {
    var title = tokens(item.title);
    var year = observedYear(item, title);
    var quality = '';
    var q = clean(item.video_quality);
    if (/^(2160|1080|720|480)$/.test(q)) quality = q === '2160' ? '4K / 2160p' : q + 'p';
    else {
      var match = title.match(/(?:^|[^\d])(2160|1080|720|480)p?\b|(?:^|[\s.])4K\b/i);
      if (match) quality = match[1] === '2160' || /4K/i.test(match[0]) ? '4K / 2160p' : match[1] + 'p';
    }
    var translation = '';
    var speech = clean(item.voices);
    if (/дубляж|дублирован|dubbed|\bDUB\b/i.test(title + ' ' + speech)) translation='Дублированный';
    else if (/многоголос|MVO/i.test(title + ' ' + speech)) translation='Многоголосый';
    else if (/одноголос|авторск|AVO/i.test(title + ' ' + speech)) translation='Авторский/одноголосый';
    else if (/оригинал|original/i.test(title + ' ' + speech)) translation='Оригинальная дорожка';
    var studios = [
      ['LostFilm', /lostfilm/i], ['NewStudio', /newstudio/i],
      ['HDrezka Studio', /hdrezka/i], ['Кубик в Кубе', /кубик[а-я\s]*в[а-я\s]*кубе/i],
      ['Jaskier', /jaskier/i], ['ColdFilm', /coldfilm/i], ['BaibaKo', /baibako/i]
    ];
    var studio = '';
    for (var i=0;i<studios.length;i++) if (studios[i][1].test(title+' '+speech)) { studio=studios[i][0]; break; }
    if (!studio && speech && !/^(rus|eng|russian|english|дублированный|дубляж|оригинал|многоголосый)$/i.test(speech)) {
      studio = speech.split(/[,;|]/)[0].slice(0, 50);
    }
    var source = '';
    var options = [
      ['REMUX', /remux/i],['Blu-ray', /blu[\s-]?ray/i],['BDRip', /bdrip/i],
      ['WEB-DL', /web[\s-]?dl/i],['WEBRip', /webrip/i],['DVDRip', /dvdrip/i],['HDTV', /hdtv/i]
    ];
    for (var j=0;j<options.length;j++) if (options[j][1].test(title)) {source=options[j][0];break;}
    var codec = /\b(HEVC|H\.?265|x265)\b/i.test(title) ? 'HEVC / H.265' :
                /\b(H\.?264|x264|AVC)\b/i.test(title) ? 'H.264 / AVC' : '';
    var hdr = /\b(dolby[\s.]?vision|\bDV\b)/i.test(title) ? 'Dolby Vision' :
              /\bHDR10\+?\b/i.test(title) ? 'HDR10' : /\bHDR\b/i.test(title) ? 'HDR' : '';
    var tags = Array.isArray(item.content_types) ? item.content_types.join(' ') : '';
    var type = /аниме/i.test(tags) ? 'Аниме' :
               /сериал|tv[\s-]?series/i.test(tags) ? 'Сериал' :
               /мультфильм|анимаци/i.test(tags) ? 'Мультфильм' :
               /фильм|movie/i.test(tags) ? 'Фильм' : '';
    return {film:observedFilm(item,title,year),year:year,quality:quality,
            translation:translation,studio:studio,source:source,codec:codec,hdr:hdr,
            type:type,country:clean(item.country)};
  }
  function TorrentFilters(node, changed) {
    this.node = node;
    this.changed = changed;
    this.items = [];
    this.facets = [];
    this.selected = {};
    this.minSize = '';
    this.maxSize = '';
    this.minSeeds = '';
    this.advanced = false;
    var self=this;
    node.addEventListener('change', function (ev) {
      var field=ev.target.getAttribute('data-filter');
      if (!field) return;
      if (field==='minSize') self.minSize=ev.target.value;
      else if (field==='maxSize') self.maxSize=ev.target.value;
      else if (field==='minSeeds') self.minSeeds=ev.target.value;
      else self.selected[field]=ev.target.value;
      self.changed();
    });
    node.addEventListener('click',function(ev){
      if (ev.target.getAttribute('data-reset-filters')!==null) {
        self.reset(); self.render(); self.changed();
      }
    });
  }
  TorrentFilters.prototype.reset = function() {
    this.selected={};this.minSize='';this.maxSize='';this.minSeeds='';
  };
  TorrentFilters.prototype.setResults = function(items) {
    this.items = items || [];
    this.facets = this.items.map(observe);
    this.render();
  };
  TorrentFilters.prototype.filter = function(items) {
    var self=this;
    return items.filter(function(item,index) {
      var meta=self.facets[index] || observe(item);
      for(var field in self.selected) {
        if (self.selected[field] && self.selected[field]!==ALL) {
          if ((meta[field] || UNKNOWN)!==self.selected[field]) return false;
        }
      }
      var size=Number(item.size||0)/GB, seeds=Number(item.seeders||0);
      if (self.minSize!=='' && (!item.size || size<Number(self.minSize))) return false;
      if (self.maxSize!=='' && (!item.size || size>Number(self.maxSize))) return false;
      if (self.minSeeds!=='' && seeds<Number(self.minSeeds)) return false;
      return true;
    });
  };
  TorrentFilters.prototype.render = function() {
    var self=this;
    if (!this.items.length) {this.node.innerHTML='';this.node.classList.add('hidden');return;}
    this.node.classList.remove('hidden');
    var controls=fields.map(function(f) {
      var key=f[0], values=[];
      self.facets.forEach(function(meta) {var v=meta[key] || UNKNOWN; if(values.indexOf(v)<0) values.push(v);});
      if (values.length<=1 && values[0]===UNKNOWN) return '';
      values.sort(function(a,b){return a.localeCompare(b,'ru',{numeric:true});});
      var options=['<option value="'+ALL+'">Все</option>'].concat(values.map(function(v){
        var title=v===UNKNOWN?'Не указано':v;
        return '<option value="'+html(v)+'"'+(self.selected[key]===v?' selected':'')+'>'+html(title)+'</option>';
      }));
      return '<label class="field"><span>'+html(f[1])+'</span><select data-filter="'+key+'">'+options.join('')+'</select></label>';
    }).filter(Boolean);
    var numeric = [
      ['minSize','Размер от (ГБ)',this.minSize],['maxSize','Размер до (ГБ)',this.maxSize],
      ['minSeeds','Минимум сидов',this.minSeeds]
    ].map(function(f) {
      return '<label class="field"><span>'+f[1]+'</span><input type="number" min="0" step="'+
         (f[0]==='minSeeds'?'1':'0.5')+'" data-filter="'+f[0]+'" value="'+html(f[2])+
         '" placeholder="Не ограничено"></label>';
    });
    this.node.innerHTML='<div class="flex between"><h3>Уточнить найденные раздачи</h3>'+
      '<button type="button" class="btn small" data-reset-filters>Сбросить</button></div>'+
      '<p class="muted">Значения получены из текущей выдачи. Фильтры применяются без нового поиска.</p>'+
      '<div class="two foot">'+controls.concat(numeric).join('')+'</div>';
  };
  window.TorrentFilters=TorrentFilters;
})();
