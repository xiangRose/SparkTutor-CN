'use strict';

window.createSparkTutorCheckIn = function ({rpc, esc, icon, toast}) {
  let host = null, data = null, pending = false, error = '', version = 0, timer = null;

  function calendar(status) {
    const checked = new Set(status.checkedDates);
    const cells = [];
    for (let index = 0; index < status.firstWeekday; index++) cells.push('<td aria-hidden="true"></td>');
    for (let day = 1; day <= status.daysInMonth; day++) {
      const value = `${status.month}-${String(day).padStart(2, '0')}`;
      const signed = checked.has(value), today = value === status.today, future = value > status.today;
      const label = `${value}${today ? '，今天' : ''}${signed ? '，已签到' : future ? '' : '，未签到'}`;
      cells.push(`<td><time datetime="${value}" class="checkin-day${signed ? ' is-checked' : ''}${today ? ' is-today' : ''}${future ? ' is-future' : ''}" aria-label="${label}"${today ? ' aria-current="date"' : ''}>${day}${signed ? icon('check') : ''}</time></td>`);
    }
    while (cells.length % 7) cells.push('<td aria-hidden="true"></td>');
    let rows = '';
    for (let index = 0; index < cells.length; index += 7) rows += `<tr>${cells.slice(index, index + 7).join('')}</tr>`;
    return `<table class="checkin-calendar" aria-label="${esc(status.month)} 签到日历"><thead><tr>${['一','二','三','四','五','六','日'].map(day => `<th scope="col">${day}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table>`;
  }

  function render() {
    if (!host?.isConnected) return;
    const header = `<div class="aside-heading"><span class="aside-icon">${icon('calendar')}</span><h3>学习签到</h3><span class="checkin-habit">每天进步一点</span></div>`;
    if (!data) {
      host.innerHTML = header + (error
        ? `<p class="checkin-error" role="alert">${esc(error)}</p><button class="btn small" data-checkin-action="refresh"${pending ? ' disabled' : ''}>重新读取</button>`
        : '<div class="busy-indicator"><span class="spinner"></span>正在读取签到记录…</div>');
      return;
    }
    const [year, month] = data.month.split('-').map(Number);
    const calendarOpen = Boolean(host.querySelector?.('.checkin-calendar-details')?.open);
    host.innerHTML = `${header}<p class="checkin-subtitle">点击下方按钮，记录今天的学习。</p>
      <div class="checkin-stats"><div><strong>${data.currentStreak}<small>天</small></strong><span>连续签到</span></div><div><strong>${data.monthCount}<small>天</small></strong><span>${data.month === data.today.slice(0, 7) ? '本月签到' : '该月签到'}</span></div><div><strong>${data.totalDays}<small>天</small></strong><span>累计签到</span></div></div>
      <button class="btn primary checkin-button${data.checkedInToday ? ' is-complete' : ''}" data-checkin-action="sign"${pending || data.checkedInToday ? ' disabled' : ''}>${icon(data.checkedInToday ? 'check' : 'calendar')}${pending ? '正在读取…' : data.checkedInToday ? '今日已签到' : '点击签到'}${data.checkedInToday ? '<span>明天继续</span>' : ''}</button>
      <details class="checkin-calendar-details"${calendarOpen ? ' open' : ''}><summary>签到日历<span>${year} 年 ${month} 月 ${icon('arrow')}</span></summary>
        <div class="checkin-month"><button class="icon-button" data-checkin-action="previous" aria-label="上个月"${pending || (year === 1 && month === 1) ? ' disabled' : ''}>${icon('arrow')}</button><strong>${year} 年 ${month} 月</strong><button class="icon-button" data-checkin-action="next" aria-label="下个月"${pending || data.month >= data.today.slice(0, 7) ? ' disabled' : ''}>${icon('arrow')}</button></div>
        ${calendar(data)}
        <div class="checkin-legend"><span><i></i>已签到</span><span><i class="today-marker"></i>今天</span><span>北京时间 · 每天一次</span></div>
      </details>
      ${error ? `<p class="checkin-error" role="alert">${esc(error)}</p><button class="btn small" data-checkin-action="refresh">重新读取</button>` : ''}
      <p class="checkin-note">只记录学习习惯，不影响课程成绩。</p>`;
  }

  async function refresh(month = data?.month) {
    if (!host?.isConnected || pending) return;
    const own = ++version;
    pending = true; error = ''; render();
    try {
      const result = await rpc('getCheckInStatus', month ? {month} : {});
      if (own === version && host?.isConnected) data = result;
    } catch (failure) {
      if (own === version) error = failure.message || '暂时无法读取签到记录，请重试。';
    } finally {
      if (own === version) { pending = false; render(); }
    }
  }

  async function sign() {
    if (pending || data?.checkedInToday || !host?.isConnected) return;
    const own = ++version;
    pending = true; error = ''; render();
    try {
      const result = await rpc('checkIn');
      if (own !== version || !host?.isConnected) return;
      data = result;
      toast(result.recorded ? '签到成功，今天也在稳稳进步。' : '今天已经签到过了，明天继续。');
    } catch (failure) {
      if (own === version) error = failure.message || '签到未完成，请重试。';
    } finally {
      if (own === version) { pending = false; render(); }
    }
  }

  function shiftMonth(delta) {
    const [year, month] = data.month.split('-').map(Number);
    const absolute = year * 12 + month - 1 + delta;
    return `${String(Math.floor(absolute / 12)).padStart(4, '0')}-${String(absolute % 12 + 1).padStart(2, '0')}`;
  }

  function onClick(event) {
    const button = event.target.closest('[data-checkin-action]');
    if (!button || button.disabled || pending) return;
    if (button.dataset.checkinAction === 'sign') sign();
    else if (button.dataset.checkinAction === 'previous') refresh(shiftMonth(-1));
    else if (button.dataset.checkinAction === 'next') refresh(shiftMonth(1));
    else refresh();
  }

  function onVisible() { if (!document.hidden) refresh(); }
  function destroy() {
    version++; clearInterval(timer); timer = null;
    host?.removeEventListener('click', onClick);
    document.removeEventListener('visibilitychange', onVisible);
    window.removeEventListener('focus', onVisible);
    host = null; data = null; error = ''; pending = false;
  }

  return {
    mount(element) {
      destroy(); host = element;
      host.addEventListener('click', onClick);
      document.addEventListener('visibilitychange', onVisible);
      window.addEventListener('focus', onVisible);
      timer = setInterval(onVisible, 60000);
      render(); refresh();
    },
    destroy,
  };
};
