-- 003_card_decks_immutable.sql —— 卡组版本不可改写（CARD-001）。
-- 卡组按（来源类型, 来源 id, 来源版本）派生：内容变了只追加新版本，旧版本原样保留。
-- 服务层从不 UPDATE / DELETE card_decks；这里在数据库层兜底，防止任何旁路改写已发布的卡组版本。
CREATE TRIGGER card_decks_no_update BEFORE UPDATE ON card_decks
BEGIN SELECT RAISE(ABORT,'CARD_DECK_VERSION_IMMUTABLE'); END;
CREATE TRIGGER card_decks_no_delete BEFORE DELETE ON card_decks
BEGIN SELECT RAISE(ABORT,'CARD_DECK_VERSION_IMMUTABLE'); END;
