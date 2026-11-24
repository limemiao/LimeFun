/* Offline premiere metadata: no third-party requests or scheduled updates. */
(() => {
  const labels = {'1':'1月番', '4':'4月番', '7':'7月番', '10':'10月番', unknown:'季度未知'};
  const classify = (item, snapshot) => {
    const record = snapshot?.items?.[String(item.id)];
    const date = record?.date || '';
    const match = /^(\d{4})-(0[1-9]|1[0-2])(?:-\d{2})?$/.exec(date);
    const known = record?.status === 'matched' && match;
    return {
      ...item,
      premiere_date: known ? date : '',
      premiere_source: known ? record.source_url || '' : '',
      season: known ? String(1 + 3 * Math.floor((Number(match[2]) - 1) / 3)) : 'unknown',
      filter_year: known ? match[1] : String(item.vod_year || ''),
    };
  };
  const api = {labels, classify};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else window.LimeSeasons = api;
})();
