// ┬  ┬┌─┐┌┬┐┌─┐
// │  │└─┐ │ └─┐
// ┴─┘┴└─┘ ┴ └─┘

// Print the first List
const printFirstList = () => {
  let icon = `<i class="list__head" icon-name="${CONFIG.firstListIcon}"></i>`;
  const position = 'beforeend';
  list_1.insertAdjacentHTML(position, icon);
  for (const link of CONFIG.lists.firstList) {
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
    list_1.insertAdjacentHTML(position, item);
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
