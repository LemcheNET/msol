// msol helper: when a Microsoft app window (an --app window) opens a link in a
// new browser tab, hand the link to msol so it opens as its own app window
// (with its own taskbar entry), and close the browser tab.
//
// Only links opened FROM an app window are considered; tabs and windows you
// open yourself in a normal browser window are left alone. msol decides per
// link: a Microsoft app it knows opens as that app, anything else stays.

const HOST = "com.msol.open";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// A brand-new window may not be visible to the API for a moment: retry briefly.
async function windowTypeOfTab(tabId) {
  for (let i = 0; i < 20; i++) {
    try {
      const tab = await chrome.tabs.get(tabId);
      return (await chrome.windows.get(tab.windowId)).type;
    } catch {
      await sleep(50);
    }
  }
  return null;
}

function handOver(tabId, url) {
  if (!/^https:\/\//.test(url)) return;
  chrome.runtime.sendNativeMessage(HOST, { url }, (reply) => {
    if (chrome.runtime.lastError) {
      console.warn("msol host unavailable:", chrome.runtime.lastError.message);
      return;                                   // leave the tab as it is
    }
    if (reply && reply.action === "opened") chrome.tabs.remove(tabId).catch(() => {});
  });
}

// Fired when a page opens a link in a new tab or window (target=_blank,
// window.open, ...), with both the source and the new tab.
chrome.webNavigation.onCreatedNavigationTarget.addListener(async (d) => {
  const [source, target] = await Promise.all([windowTypeOfTab(d.sourceTabId), windowTypeOfTab(d.tabId)]);
  if (source && source !== "normal" && target === "normal") handOver(d.tabId, d.url);
});
