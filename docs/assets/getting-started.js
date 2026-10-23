/* 自定义选择器筛选已存在的教程内容，不收集或保存用户凭据。 */
(() => {
  // 当前页的完整教程容器。
  const guide = document.querySelector('.guide');
  if (!guide) return;
  // 操作系统、数据库选择及可访问的反馈区域。
  const osSelect = guide.querySelector('#guide-os');
  // 数据库路径决定每条 Compose 命令使用的配置文件。
  const dbSelect = guide.querySelector('#guide-db');
  // 当前选择的文字说明。
  const selection = guide.querySelector('#guide-selection');
  // 复制结果的全局播报区域。
  const copyStatus = guide.querySelector('#guide-copy-status');
  // 原生目录在桌面展开，在手机上可按需展开。
  const toc = guide.querySelector('.guide-toc');
  // 与 CSS 使用同一宽度断点。
  const narrow = window.matchMedia('(max-width: 860px)');
  // 保存菜单关闭方法，保证同时只展开一个菜单。
  const closeMenus = [];

  // 用可键盘操作的菜单增强现有选项，原 select 仅保存筛选值。
  function enhanceSelect(select) {
    // 选择器所属字段。
    const field = select.parentElement;
    // 菜单触发按钮，焦点始终保留在此处。
    const trigger = document.createElement('button');
    // 自定义选项列表。
    const menu = document.createElement('div');
    // 当前键盘活动选项，不在移动时立即修改已提交的值。
    let activeIndex = select.selectedIndex;
    trigger.type = 'button';
    trigger.className = 'guide-select-trigger';
    trigger.id = `${select.id}-trigger`;
    trigger.setAttribute('role', 'combobox');
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');
    trigger.setAttribute('aria-labelledby', `${select.id}-label ${trigger.id}`);
    trigger.setAttribute('aria-controls', `${select.id}-menu`);
    menu.id = `${select.id}-menu`;
    menu.className = 'guide-select-menu';
    menu.setAttribute('role', 'listbox');
    menu.setAttribute('aria-labelledby', `${select.id}-label`);
    menu.hidden = true;

    // 自定义选项与原始 option 一一对应。
    const options = Array.from(select.options, (option, index) => {
      // 单个可点击选项，键盘焦点由触发按钮管理。
      const item = document.createElement('div');
      item.className = 'guide-select-option';
      item.id = `${select.id}-option-${index}`;
      item.setAttribute('role', 'option');
      item.tabIndex = -1;
      item.textContent = option.textContent;
      item.addEventListener('pointermove', () => setActive(index));
      item.addEventListener('click', () => commit(index));
      menu.append(item);
      return item;
    });

    // 同步按钮文本与已选状态。
    function render() {
      trigger.textContent = select.selectedOptions[0].textContent;
      options.forEach((item, index) => item.setAttribute('aria-selected', String(index === select.selectedIndex)));
    }

    // 关闭时清除活动选项引用，取消未提交的键盘选择。
    function close() {
      menu.hidden = true;
      trigger.setAttribute('aria-expanded', 'false');
      trigger.removeAttribute('aria-activedescendant');
    }

    // 标记活动选项，并让长菜单中的选项保持可见。
    function setActive(index) {
      activeIndex = index;
      options.forEach((item, position) => item.classList.toggle('is-active', position === index));
      trigger.setAttribute('aria-activedescendant', options[index].id);
      options[index].scrollIntoView({ block: 'nearest' });
    }

    // 打开前关闭其他菜单，并按视口空间决定向上或向下展开。
    function open() {
      closeMenus.forEach((closeMenu) => closeMenu());
      menu.hidden = false;
      trigger.setAttribute('aria-expanded', 'true');
      // 按钮在当前视口中的位置。
      const bounds = trigger.getBoundingClientRect();
      // 空间不足时改为向上展开。
      const above = innerHeight - bounds.bottom < menu.offsetHeight + 8 && bounds.top > menu.offsetHeight + 8;
      menu.style.top = above ? `${trigger.offsetTop - menu.offsetHeight - 8}px` : 'calc(100% + 8px)';
      setActive(select.selectedIndex);
    }

    // 提交后复用已有 change 处理，统一更新教程命令。
    function commit(index) {
      select.selectedIndex = index;
      select.dispatchEvent(new Event('change', { bubbles: true }));
      close();
      trigger.focus();
    }

    trigger.addEventListener('click', () => menu.hidden ? open() : close());
    trigger.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        close();
      } else if (event.key === 'Tab') {
        close();
      } else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
        event.preventDefault();
        if (menu.hidden) open();
        if (event.key === 'Home') setActive(0);
        else if (event.key === 'End') setActive(options.length - 1);
        else setActive((activeIndex + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length);
      } else if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        if (menu.hidden) open();
        else commit(activeIndex);
      }
    });
    field.addEventListener('focusout', (event) => {
      if (!field.contains(event.relatedTarget)) close();
    });
    document.addEventListener('pointerdown', (event) => {
      if (!field.contains(event.target)) close();
    });
    select.addEventListener('change', render);
    closeMenus.push(close);
    select.hidden = true;
    field.append(trigger, menu);
    render();
  }

  // 只隐藏不相关分支，使无脚本阅读仍保留完整教程。
  function updateBranches() {
    // 当前系统标识。
    const os = osSelect.value;
    // 当前数据库路径标识。
    const db = dbSelect.value;
    guide.querySelectorAll('[data-os]').forEach((element) => {
      element.hidden = element.dataset.os !== os && !(element.dataset.os === 'unix' && os !== 'windows');
    });
    guide.querySelectorAll('[data-db]').forEach((element) => {
      element.hidden = element.dataset.db !== db;
    });
    selection.textContent = `当前教程：${osSelect.selectedOptions[0].textContent} · ${dbSelect.selectedOptions[0].textContent}。下方命令已对应此选择。`;
  }

  // 根据屏幕宽度设置目录的初始展开状态。
  function updateToc() {
    toc.open = !narrow.matches;
  }

  // 复制当前代码块；拒绝权限时保留文本与清晰的手动操作提示。
  async function copyCode(event) {
    // 用户刚点击的复制按钮。
    const button = event.target.closest('.guide-copy');
    if (!button) return;
    // 只读取这个按钮所属代码块中的文本。
    const code = button.closest('.guide-code').querySelector('code');
    try {
      await navigator.clipboard.writeText(code.textContent);
      button.textContent = '已复制';
      copyStatus.textContent = '命令已复制到剪贴板。';
    } catch {
      button.textContent = '请手动复制';
      copyStatus.textContent = '无法访问剪贴板，请选中代码文本手动复制。';
      code.parentElement.focus();
    }
  }

  guide.classList.add('guide-enhanced');
  osSelect.addEventListener('change', updateBranches);
  dbSelect.addEventListener('change', updateBranches);
  enhanceSelect(osSelect);
  enhanceSelect(dbSelect);
  guide.addEventListener('click', copyCode);
  narrow.addEventListener('change', updateToc);
  updateBranches();
  updateToc();
})();
