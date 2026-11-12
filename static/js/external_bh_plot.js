(() => {
  const NS = 'http://www.w3.org/2000/svg';
  const style = {
    measured: ['#1f2937', '', 2.1], fit: ['#526579', '6 4', 1.7],
    holdout: ['#174ea6', '', 2.6], secondary_fit: ['#724491', '2 4', 1.7],
    secondary_holdout: ['#a84612', '8 4', 2.6], candidate: ['#087253', '', 2.6],
  };
  const number = value => String(Number(value.toPrecision(6)));
  function graph(svg, legend, data) {
    const [lo, hi] = data.H_range_A_per_m;
    const allB = data.series.flatMap(series => series.line.map(pair => pair[1]));
    const y0 = Math.min(0, ...allB);
    const y1 = Math.max(...allB);
    const span = y1 - y0;
    const top = span > 0 ? y1 + 0.07 * span : y0 + 1;
    if (!(lo < hi && y0 < top)) throw new Error('绘图坐标范围无效');
    const x = h => 70 + (h-lo)*562/(hi-lo);
    const y = b => 310 - (b-y0)*277/(top-y0);
    const add = (parent, tag, attrs, text) => {
      const element = document.createElementNS(NS, tag);
      for (const [name,value] of Object.entries(attrs)) element.setAttribute(name, value);
      if (text !== undefined) element.textContent = text;
      parent.append(element);
      return element;
    };
    svg.replaceChildren();
    legend.replaceChildren();
    svg.setAttribute('aria-label', data.direction+' 原表和保存 B–H 曲线对照');
    add(svg,'rect',{x:70,y:33,width:562,height:277,fill:'#ffffff',stroke:'#64748b'});
    for (let i=0;i<=5;i++) {
      const hx=lo+(hi-lo)*i/5, by=y0+(top-y0)*i/5;
      add(svg,'line',{x1:x(hx),y1:33,x2:x(hx),y2:310,stroke:'#e2e8f0'});
      add(svg,'line',{x1:70,y1:y(by),x2:632,y2:y(by),stroke:'#e2e8f0'});
      add(svg,'text',{x:x(hx),y:329,'text-anchor':'middle',fill:'#334155'},number(hx));
      add(svg,'text',{x:62,y:y(by)+4,'text-anchor':'end',fill:'#334155'},number(by));
    }
    add(svg,'text',{x:350,y:355,'text-anchor':'middle',fill:'#1e293b'},'H (A/m)');
    add(svg,'text',{x:17,y:172,transform:'rotate(-90 17 172)','text-anchor':'middle',fill:'#1e293b'},'B (T)');
    for (const series of data.series) {
      const [stroke,dash,width] = style[series.key] || style.fit;
      add(svg,'polyline',{points:series.line.map(([h,b]) => x(h)+','+y(b)).join(' '),fill:'none',
        stroke,'stroke-width':width,'stroke-dasharray':dash,'data-bh-series':series.key});
      const item=document.createElement('li');
      const sample=document.createElement('span');
      sample.className='bh-legend-line';
      sample.style.borderTopColor=stroke;
      sample.style.borderTopStyle=dash?'dashed':'solid';
      item.append(sample,document.createTextNode(series.label));
      legend.append(item);
    }
    for (const [h,b,row] of data.points) {
      const marker=add(svg,'circle',{cx:x(h),cy:y(b),r:3.2,fill:style.measured[0],
        stroke:'#ffffff','stroke-width':0.7,'data-bh-marker':row});
      add(marker,'title',{},'H '+number(h)+' A/m, B '+number(b)+' T, 原行 '+row);
    }
    svg.removeAttribute('hidden');
  }
  document.querySelectorAll('[data-bh-plot]').forEach(panel => {
    const dir=panel.querySelector('[data-bh-direction]');
    const scope=panel.querySelector('[data-bh-scope]');
    const status=panel.querySelector('[data-bh-status]');
    const svg=panel.querySelector('[data-bh-svg]');
    const legend=panel.querySelector('[data-bh-legend]');
    let generation=0, active='', completed='';
    const checkScope = () => {
      const hasFocus=dir.selectedOptions[0].dataset.focus==='yes';
      scope.options[1].disabled=!hasFocus;
      if (!hasFocus) scope.value='common';
      return hasFocus;
    };
    if (checkScope()) scope.value='focus';
    const refresh=async () => {
      checkScope();
      if (!panel.open) return;
      const key=dir.value+'|'+scope.value;
      if (key===completed || key===active) return;
      const request=++generation;
      active=key;
      completed='';
      svg.setAttribute('hidden','');
      legend.replaceChildren();
      status.textContent='正在读取保存的曲线。';
      try {
        const url=new URL(panel.dataset.bhPlot,window.location.origin);
        url.searchParams.set('direction',dir.value);
        url.searchParams.set('scope',scope.value);
        const response=await fetch(url);
        if (!response.ok) throw new Error('该方向没有可绘制的保存区间。');
        const data=await response.json();
        if (request!==generation) return;
        graph(svg,legend,data);
        const [low,high]=data.H_range_A_per_m;
        status.textContent='实际范围 '+number(low)+'–'+number(high)+' A/m，'+data.original_points_in_range+
          ' 个原采样点。实心点为原值，边界可能由线性插值得到。'+
          (data.display_downsampled?'图上抽样显示；完整原点仍在下载包中。':'');
        completed=key;
      } catch (error) {
        if (request===generation) status.textContent=error.message || '曲线读取失败。';
      } finally {
        if (request===generation) active='';
      }
    };
    panel.addEventListener('toggle',refresh);
    dir.addEventListener('change',refresh);
    scope.addEventListener('change',refresh);
  });
})();
