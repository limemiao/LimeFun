/* Small, framework-free controls shared by the two local applications. */
(() => {
  const main = document.querySelector('main');
  if (main) {
    main.id ||= 'mainContent';
    const skip = Object.assign(document.createElement('a'), {className: 'skip-link', href: `#${main.id}`, textContent: '跳到主要内容'});
    document.body.prepend(skip);
  }
  document.querySelectorAll('.topnav a.active,.page-link.is-active').forEach(a => a.setAttribute('aria-current', 'page'));
  document.querySelectorAll('button').forEach(button => { if (!button.type) button.type = 'button'; });
  const search = document.getElementById('catalogSearch');
  const filterBar = document.querySelector('.catalog-filter-bar,.filter-bar');
  if (filterBar) {
    filterBar.id = 'catalogFilters';
    filterBar.classList.add('filters-collapsed');
    const toggle = Object.assign(document.createElement('button'), {type:'button',className:'filter-toggle',textContent:'展开筛选'});
    toggle.setAttribute('aria-controls',filterBar.id);
    toggle.setAttribute('aria-expanded','false');
    toggle.addEventListener('click', () => {
      const collapsed = filterBar.classList.toggle('filters-collapsed');
      toggle.setAttribute('aria-expanded',String(!collapsed));
      toggle.textContent = collapsed ? '展开筛选' : '收起筛选';
    });
    document.getElementById('resetFilters')?.before(toggle);
  }
  if (search) document.addEventListener('keydown', event => {
    if (event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
      event.preventDefault(); search.focus();
    }
  });
  window.LimeUI = {
    page: 1,
    size: 36,
    paginate(items, render) {
      const pages = Math.max(1, Math.ceil(items.length / this.size));
      this.page = Math.max(1, Math.min(this.page, pages));
      let nav = document.querySelector('.pagination');
      if (!nav) {
        nav = document.createElement('nav'); nav.className = 'pagination'; nav.setAttribute('aria-label', '作品目录分页');
        document.getElementById('catalogGrid').after(nav);
      }
      nav.replaceChildren(); nav.hidden = !items.length;
      const button = (text, delta) => {
        const node = Object.assign(document.createElement('button'), {type:'button', textContent:text});
        node.disabled = delta < 0 ? this.page === 1 : this.page === pages;
        node.addEventListener('click', () => {
          this.page += delta; render();
          document.getElementById('resultCount').scrollIntoView({block:'start'});
          document.querySelector(`.pagination button:${delta < 0 ? 'first' : 'last'}-child`)?.focus({preventScroll:true});
        }); return node;
      };
      const label = document.createElement('p');
      label.setAttribute('aria-live','polite');
      label.innerHTML = `<strong>${this.page}</strong> / ${pages} 页<br>每页 ${this.size} 部`;
      nav.append(button('← 上一页', -1), label, button('下一页 →', 1));
      return items.slice((this.page - 1) * this.size, this.page * this.size);
    },
    filters(render) {
      document.querySelectorAll('[data-quick]').forEach(button => button.addEventListener('click', () => {
        const local = document.getElementById('localFilter');
        const favorite = document.getElementById('favoriteFilter');
        local.value = button.dataset.quick === 'local' ? 'yes' : '';
        if (favorite) { favorite.value = button.dataset.quick === 'favorites' ? 'yes' : ''; favorite.dispatchEvent(new Event('input')); }
        local.dispatchEvent(new Event('input'));
      }));
      document.getElementById('resetFilters')?.addEventListener('click', () => {
        const controls = document.querySelectorAll('.catalog-filter-bar input,.catalog-filter-bar select,.filter-bar input,.filter-bar select');
        controls.forEach(control => { control.value = control.id === 'sortFilter' ? 'name' : ''; control.dispatchEvent(new Event('input')); });
        search?.focus();
      });
    },
    syncFilters() {
      const local = document.getElementById('localFilter')?.value;
      const favorite = document.getElementById('favoriteFilter')?.value;
      const selected = favorite === 'yes' ? 'favorites' : local === 'yes' ? 'local' : (!local && !favorite ? 'all' : '');
      document.querySelectorAll('[data-quick]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.quick === selected)));
    }
  };
  // Complete the ARIA tab pattern without changing download behavior.
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  tabs.forEach((tab, index) => {
    const panel = document.getElementById(`${tab.dataset.view}View`);
    tab.id = `download-tab-${tab.dataset.view}`;
    if (panel) { panel.setAttribute('role','tabpanel'); panel.setAttribute('aria-labelledby', tab.id); tab.setAttribute('aria-controls', panel.id); }
    tab.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
      if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = tabs.length - 1;
      if (next !== undefined) { event.preventDefault(); tabs[next].click(); tabs[next].focus(); }
    });
    const update = () => { tab.tabIndex = tab.getAttribute('aria-selected') === 'true' ? 0 : -1; };
    new MutationObserver(update).observe(tab, {attributes:true, attributeFilter:['aria-selected']}); update();
  });
})();
