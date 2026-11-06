(a) => {
  if (location.origin !== 'https://www.zhipin.com') throw Error('Wrong origin');
  if (/环境存在风险|安全验证|账号异常|验证后继续/.test(document.body.innerText)) throw Error('Visible platform restriction');
  if (location.pathname !== '/web/geek/jobs') throw Error('Job list page required');
  if (!document.querySelector('a[href$="/web/geek/resume"]')) throw Error('Login unavailable');

  const page = document.querySelector('.page-jobs-main')?.__vue__;
  if (String(page?.$store?.state?.userInfo?.userId) !== String(a.self)) throw Error('Account mismatch');

  const url = new URL(location.href);
  const city = url.searchParams.get('city') || String(page.defaultCity?.code || '');
  if (city !== String(a.city_code)) {
    throw Error('City mismatch: select the configured city in the existing space first');
  }

  const rows = [...document.querySelectorAll('li.job-card-box')].map((node) => {
    const data = node.parentElement?.__vue__?.$props?.data;
    const href = node
      .querySelector('a.job-name')
      ?.getAttribute('href')
      ?.match(/\/job_detail\/([^/.]+)\.html/)?.[1];
    return {
      job_id: data?.encryptJobId,
      company: data?.brandName,
      boss_id: data?.encryptBossId,
      title: data?.jobName,
      salary: data?.salaryDesc,
      address: [data?.cityName, data?.areaDistrict, data?.businessDistrict].filter(Boolean).join('·'),
      href_id: href,
      job_url: href ? 'https://www.zhipin.com/job_detail/' + href + '.html' : '',
    };
  });

  return {
    query: document.querySelector('input[placeholder="搜索职位、公司"]')?.value ?? '',
    city,
    search_active: url.searchParams.get('query') === document.querySelector('input[placeholder="搜索职位、公司"]')?.value,
    rows,
  };
}
