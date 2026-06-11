// アプリ全体の共有状態。各モジュールが import して読み書きする単一の真実源。
// (Phase 4B: main.js から分離。import は読取専用なので「再代入」はせず、
//  プロパティ変更 state.x = ... で更新する — これは import 越しでも合法)
export const state = {
  tab: 'likes',                      // 'likes' | 'timeline'
  layout: 'masonry',                 // 'masonry' | 'reel'
  authors: [],
  tags: [],
  lists: [],                          // [{id, name, count}]
  filter: { author: null, tag: null, media_type: '', q: '', list_id: null },
  hideLiked: true,
  posts: [],
  total: 0,
  offset: 0,
  limit: 60,
  loading: false,
  syncRunning: false,
  lastSeenTimeline: '',               // tweet_id; '' = no marker yet
  dividerInserted: false,             // current page's divider rendered?
  lb: { items: [], pos: 0 },          // lightbox group: posts sharing tweet_id
  authorSummary: null,                // cached { author, nick, counts } for the current filter
  unliked: { active: false, author: null, items: [], offset: 0, limit: 60, hasMore: false, loading: false, loadingMore: false, error: '' },
  favoriteAuthors: new Set(),
};
