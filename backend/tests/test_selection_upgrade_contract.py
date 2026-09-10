import copy
import unittest
from unittest.mock import AsyncMock, patch
from services.stock_selection_agents import StockSelectionAgentService

class SelectionContractTests(unittest.IsolatedAsyncioTestCase):
    async def run_case(self, grades, *, sector_limit=2, top_n=3):
        stocks=[{'code':str(600001+i),'name':f'样本{i}','price':10,'sector':sector,'selection_sources':['volume']} for i,(_,sector) in enumerate(grades)]
        def analyze(stock,*args,**kwargs):
            kind,sector=grades[int(stock['code'])-600001]
            return {'code':stock['code'],'name':stock['name'],'sector':sector,'price':10,'change_pct':1,'score':90,'confidence':80,'agents':{'risk':{'structural_risk':{'hard_blocked':kind=='excluded'},'plan':{'risk_level':'低','stop_loss_price':9,'reference_target_price':12}}},'research':{'data_quality':{'grade':'不足' if kind=='watch' else '充分'},'strategy_audit':{'overall_risk':'低','blockers':[]}},'horizon_outlook':{'validation_conditions':['量价进一步确认'],'invalidation_conditions':['结构失守']}}
        svc=StockSelectionAgentService()
        svc._candidate_snapshot=AsyncMock(return_value={'stocks':stocks,'data_date':'2026-09-09','source':'eastmoney','is_realtime':False})
        svc._refresh_candidate_histories=AsyncMock(return_value={})
        svc._load_histories=AsyncMock(return_value={})
        svc._analyze_candidate=analyze
        with patch('services.stock_selection_agents.MarketRegime.detect',new=AsyncMock(return_value={'regime':'震荡','bias':'neutral','confidence':0.5})),patch('services.stock_selection_agents.macro_policy_news_collector.get_context',new=AsyncMock(return_value={})),patch('services.stock_selection_agents.macro_policy_news_collector.get_stock_announcements',new=AsyncMock(return_value={})),patch('services.stock_selection_agents.stock_feature_service.enrich',new=AsyncMock(return_value={'stocks':stocks,'coverage':{},'warnings':[]})):
            return await svc.run(top_n=top_n,sector_limit=sector_limit)

    async def test_zero_qualified_preserves_watch_and_excluded_and_parameters(self):
        result=await self.run_case([('watch','甲行业'),('excluded','乙行业')])
        self.assertFalse(result['available'])
        self.assertEqual(result['recommendations'],[])
        self.assertEqual(len(result['watchlist']),1)
        self.assertEqual(len(result['excluded']),1)
        self.assertEqual(result['qualification_summary'],{'qualified':0,'watch':1,'excluded':1})
        self.assertEqual(result['selection_parameters']['sector_limit'],2)

    async def test_sector_cap_moves_overflow_to_observation(self):
        result=await self.run_case([('qualified','甲行业'),('qualified','甲行业'),('qualified','乙行业')],sector_limit=1)
        self.assertEqual(len(result['recommendations']),2)
        self.assertEqual(len(result['watchlist']),1)
        self.assertIn('行业配额',result['watchlist'][0]['qualification']['reasons'][0])
        self.assertEqual(result['watchlist'][0]['opportunity']['status'],'waiting')

    async def test_display_cap_never_silently_discards_analyzed_stocks(self):
        result=await self.run_case([('qualified',str(i)) for i in range(5)],sector_limit=0)
        self.assertEqual(len(result['recommendations']),3)
        self.assertEqual(len(result['watchlist']),2)
        self.assertEqual(result['candidate_summary']['analyzed'],5)
