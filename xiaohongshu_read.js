/* Synchronous page steps. The MCP host owns polling and its wall-clock deadline. */
(() => {
  "use strict";
  const request = __BB_READ_REQUEST__;
  const fail = (error_code, message) => ({ status: "error", error_code, message });
  const pending = (stage, started = false) => ({ status: "pending", stage, started });
  const notePattern = /^[a-f0-9]{24}$/i;
  const allowedHosts = new Set(["xiaohongshu.com", "www.xiaohongshu.com"]);
  const noteIdFromPath = (path) => {
    const match = String(path || "").match(/^\/(?:explore|search_result)\/([a-f0-9]{24})\/?$/i);
    return match ? match[1].toLowerCase() : null;
  };

  if (!request || !["note", "comments"].includes(request.mode) || !["start", "poll"].includes(request.phase)) {
    return fail("INVALID_ARGUMENT", "mode must be note/comments and phase must be start/poll");
  }
  const input = typeof request.note_id_or_url === "string" ? request.note_id_or_url.trim() : "";
  let noteId;
  let inputToken = null;
  if (notePattern.test(input)) {
    noteId = input.toLowerCase();
  } else {
    let inputUrl;
    try { inputUrl = new URL(input); } catch { /* Rejected below. */ }
    if (!inputUrl || inputUrl.protocol !== "https:" || !allowedHosts.has(inputUrl.hostname) ||
        inputUrl.username || inputUrl.password || inputUrl.port || !(noteId = noteIdFromPath(inputUrl.pathname))) {
      return fail("INVALID_ARGUMENT", "Pass a 24-character note ID or a full HTTPS Xiaohongshu note URL");
    }
    inputToken = inputUrl.searchParams.get("xsec_token");
  }

  try {
    if (!allowedHosts.has(location.hostname)) {
      return fail("BROWSER_PAGE_MISMATCH", "This read step must run on xiaohongshu.com");
    }
    const globals = document.querySelector("#app")?.__vue_app__?.config?.globalProperties;
    const pinia = globals?.$pinia;
    const stores = pinia?._s;
    if (!stores || typeof stores.get !== "function") return pending("page_not_ready");
    const getStore = (name) => stores.get(name) || null;
    const userStore = getStore("user");
    if (!userStore || userStore.loggedIn == null) return pending("login_state_not_ready");
    if (!userStore.loggedIn) return fail("LOGIN_REQUIRED", "Log in manually in the selected Xiaohongshu browser");

    const router = globals?.$router;
    const currentRoute = () => router?.currentRoute?.value || null;
    const routeNoteId = () => noteIdFromPath(currentRoute()?.path ?? location.pathname);
    const matchingDetail = (store) => {
      const detail = store?.noteDetailMap?.[noteId];
      const foundId = detail?.note?.noteId ?? detail?.note?.note_id;
      return typeof foundId === "string" && foundId.toLowerCase() === noteId ? detail : null;
    };
    const completeNote = (detail) => {
      const note = detail?.note;
      return !!note && typeof note.desc === "string" &&
        (typeof note.title === "string" || typeof note.type === "string" || Array.isArray(note.imageList));
    };
    const completeComments = (detail) => {
      const comments = detail?.comments;
      return Array.isArray(comments?.list) &&
        (comments.list.length > 0 || comments.firstRequestFinish === true);
    };
    const readyDetail = (detail) => request.mode === "note" ? completeNote(detail) : completeComments(detail);
    const store = getStore("note");
    const detail = matchingDetail(store);

    // Only use tokens belonging to the requested note. Never use another note's URL.
    let token = inputToken || detail?.note?.xsecToken || detail?.note?.xsec_token || null;
    if (!token && routeNoteId() === noteId) {
      const routeToken = currentRoute()?.query?.xsec_token;
      if (typeof routeToken === "string") token = routeToken;
      if (!token && noteIdFromPath(location.pathname) === noteId) {
        token = new URL(location.href).searchParams.get("xsec_token");
      }
    }
    if (!token) {
      const feeds = getStore("search")?.feeds;
      if (Array.isArray(feeds)) {
        for (const item of feeds) {
          const card = item?.noteCard || item?.note_card || item;
          const itemId = item?.id ?? item?.note_id ?? card?.noteId ?? card?.note_id;
          if (typeof itemId === "string" && itemId.toLowerCase() === noteId) {
            token = item?.xsecToken ?? item?.xsec_token ?? card?.xsecToken ?? card?.xsec_token ?? null;
            if (token) break;
          }
        }
      }
    }
    if (typeof token !== "string") token = null;
    const noteUrl = token
      ? `https://www.xiaohongshu.com/explore/${noteId}?xsec_token=${encodeURIComponent(token)}&xsec_source=`
      : `https://www.xiaohongshu.com/explore/${noteId}`;

    if (routeNoteId() === noteId && detail && readyDetail(detail)) {
      if (request.mode === "comments") {
        const state = detail.comments;
        const comments = state.list.map((comment) => ({
          id: comment?.id ?? null,
          author: comment?.userInfo?.nickname ?? comment?.user_info?.nickname ?? null,
          author_id: comment?.userInfo?.userId ?? comment?.userInfo?.user_id ?? comment?.user_info?.user_id ?? null,
          content: comment?.content ?? null,
          likes: comment?.likeCount ?? comment?.like_count ?? null,
          sub_comment_count: comment?.subCommentCount ?? comment?.sub_comment_count ?? null,
          created_time: comment?.createTime ?? comment?.create_time ?? null
        }));
        return { status: "ready", data: {
          note_id: noteId, count: comments.length,
          has_more: state.hasMore ?? state.has_more ?? false,
          cursor: state.cursor ?? null, comments
        } };
      }
      const note = detail.note;
      return { status: "ready", data: {
        note_id: noteId, xsec_token: token, title: note.title ?? null,
        desc: note.desc ?? null, type: note.type ?? null, url: noteUrl,
        author: note.user?.nickname ?? null,
        author_id: note.user?.userId ?? note.user?.user_id ?? null,
        likes: note.interactInfo?.likedCount ?? null,
        comments: note.interactInfo?.commentCount ?? null,
        collects: note.interactInfo?.collectedCount ?? null,
        shares: note.interactInfo?.shareCount ?? null,
        tags: Array.isArray(note.tagList) ? note.tagList.map((tag) => tag?.name).filter(Boolean) : [],
        images: Array.isArray(note.imageList) ? note.imageList.map((image) =>
          image?.urlDefault ?? image?.urlPre ?? image?.url ?? image?.infoList?.[0]?.url
        ).filter(Boolean) : [],
        created_time: note.time ?? null, last_update_time: note.lastUpdateTime ?? null,
        ip_location: note.ipLocation ?? null
      } };
    }

    const waitingStage = routeNoteId() !== noteId ? "navigation_pending"
      : request.mode === "comments" ? "comments_pending" : "note_detail_pending";
    if (request.phase === "poll") return pending(waitingStage, true);
    if (!token) return fail("MISSING_XSEC_TOKEN", "Pass the full note URL from a search result, including its xsec_token");
    if (!router || typeof router.push !== "function") return pending("router_not_ready");

    // Trigger page-owned read actions without keeping a CDP evaluation pending.
    const triggerDetail = () => {
      if (routeNoteId() !== noteId) return;
      const currentStore = getStore("note");
      if (!currentStore || readyDetail(matchingDetail(currentStore))) return;
      if (typeof currentStore.getNoteDetailByNoteId === "function") {
        const selection = currentStore.setCurrentNoteId?.(noteId);
        if (selection && typeof selection.catch === "function") selection.catch(() => {});
        const result = currentStore.getNoteDetailByNoteId(noteId);
        if (result && typeof result.catch === "function") result.catch(() => {});
      }
    };
    if (routeNoteId() !== noteId) {
      const navigation = router.push({ path: `/explore/${noteId}`, query: { xsec_token: token, xsec_source: "" } });
      if (navigation && typeof navigation.then === "function") {
        navigation.then(triggerDetail).catch(() => {});
      } else {
        triggerDetail();
      }
      return pending("navigation_started", true);
    }
    if (!store) return pending("note_store_not_ready");
    triggerDetail();
    return pending(waitingStage, true);
  } catch {
    return fail("PAGE_STATE_ERROR", "Could not read or start loading the requested Xiaohongshu note");
  }
})()
