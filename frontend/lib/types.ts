export interface FlowRankItem {
  rank: number;
  code: string;
  name: string;
  close_price: number;
  change_pct: number;
  main_net_inflow: number;
  main_net_inflow_pct: number;
  super_large_net_inflow: number;
  large_net_inflow: number;
  medium_net_inflow?: number;
  up_count: number;
  down_count: number;
  leading_stock: string;
}

export interface FlowRankResponse {
  code: number;
  data: {
    trade_date: string;
    update_time?: string;
    rankings: FlowRankItem[];
    summary?: {
      total_main_inflow: number;
      inflow_board_count: number;
      outflow_board_count: number;
    };
  };
}

export interface TermItem {
  id: number;
  term: string;
  category: string;
  simple_explanation: string;
  professional_explanation: string;
  usage_guide: string;
  related_terms: string[];
  difficulty_level: number;
}

export interface CaseStep {
  title: string;
  content: string;
  key_point: string;
}

export interface CaseQuiz {
  question: string;
  options: string[];
  answer: string;
  explanation: string;
}

export interface CaseItem {
  id: number;
  title: string;
  summary: string;
  event_date: string;
  category: string;
  difficulty_level: number;
  steps: CaseStep[];
  quiz: CaseQuiz;
  related_terms: string[];
  key_learnings: string[];
  view_count: number;
}

export interface BoardEncyclopedia {
  code: string;
  name: string;
  description: string;
  one_liner: string;
  simple_explanation: string;
  industry_chain: Record<string, string[]>;
  key_companies: { code: string; name: string; role: string }[];
  leading_stocks: string[];
  stock_count: number;
  triggers: string[];
  beginner_tip: string;
  related_reading: string[];
}

export interface MarketSummary {
 [key: string]: {
    date: string;
    main_net_inflow: number;
    small_net_inflow: number;
    medium_net_inflow: number;
    large_net_inflow: number;
    super_large_net_inflow: number;
  };
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface AIReport {
  date: string;
  report: string;
}
