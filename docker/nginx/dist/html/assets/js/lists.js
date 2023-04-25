// ┬  ┬┌─┐┌┬┐┌─┐
// │  │└─┐ │ └─┐
// ┴─┘┴└─┘ ┴ └─┘

// Print the first List
const isLinkAvailable = async (link) => {
  try {
    const response = await fetch(link, { method: 'HEAD', mode: 'no-cors' });
    if (response.ok) {
      // The link is available
      return true;
    } else if (response.status === 301 || response.status === 302) {
      // The link is a redirect, follow the redirect and check the final location
      const newLocation = response.headers.get('Location');
      if (newLocation) {
        const newResponse = await fetch(newLocation, { method: 'HEAD', mode: 'no-cors' });
        if (newResponse.ok) {
          // The final location is available
          return true;
        }
      }
    }
  } catch (error) {
    console.error('Link check failed: ', error);
  }
  // The link is not available
  return false;
};

const printFirstList = async () => {
  let icon = `<i class="list__head" icon-name="${CONFIG.firstListIcon}"></i>`;
  const position = 'beforeend';
  list_1.insertAdjacentHTML(position, icon);
  for (const link of CONFIG.lists.firstList) {
    let item = `
        <a
        target="${CONFIG.openInNewTab ? '_blank' : ''}"
        href="${link.link}"
        class="list__link"
        >${link.name}</a
        >
    `;
    if (await isLinkAvailable(link.link)) {
      const position = 'beforeend';
      list_1.insertAdjacentHTML(position, item);
    }
  }
};

// Print the second List
const printSecondList = () => {
  let icon = `<i class="list__head" icon-name="${CONFIG.secondListIcon}"></i>`;
  const position = 'beforeend';
  list_2.insertAdjacentHTML(position, icon);
  for (const link of CONFIG.lists.secondList) {
    // List item
    let item = `
          <a
          target="${CONFIG.openInNewTab ? '_blank' : ''}"
          href="${link.link}"
          class="list__link"
          >${link.name}</a
          >
      `;
    const position = 'beforeend';
    list_2.insertAdjacentHTML(position, item);
  }
};

printFirstList();
printSecondList();
