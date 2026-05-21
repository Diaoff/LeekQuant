import type * as monaco from 'monaco-editor'

// MyTT function completion metadata
// Source: https://github.com/mpquant/MyTT

export interface MyTTFunc {
  name: string
  params: string
  returns: string
  doc: string
  example?: string
  category: 'core' | 'apply' | 'indicator'
}

export const MYTT_FUNCTIONS: MyTTFunc[] = [
  // ===== Level 0: Core Functions =====
  {
    name: 'RD',
    params: 'N: number, D?: number = 3',
    returns: 'number[]',
    doc: 'Round to D decimal places (default 3). Equivalent to np.round().',
    example: 'RD(MA(C, 5), 2)',
    category: 'core',
  },
  {
    name: 'RET',
    params: 'S: number[], N?: number = 1',
    returns: 'number',
    doc: 'Return the Nth value from the end of sequence S. Default returns the last value.',
    example: 'RET(C, 1)  // last close price',
    category: 'core',
  },
  {
    name: 'ABS',
    params: 'S: number[]',
    returns: 'number[]',
    doc: 'Return absolute values of sequence S.',
    example: 'ABS(CLOSE - REF(CLOSE, 1))',
    category: 'core',
  },
  {
    name: 'LN',
    params: 'S: number[]',
    returns: 'number[]',
    doc: 'Return natural logarithm (base e) of sequence S.',
    example: 'LN(CLOSE)',
    category: 'core',
  },
  {
    name: 'POW',
    params: 'S: number[], N: number',
    returns: 'number[]',
    doc: 'Return S raised to power N.',
    example: 'POW(CLOSE, 2)',
    category: 'core',
  },
  {
    name: 'SQRT',
    params: 'S: number[]',
    returns: 'number[]',
    doc: 'Return square root of sequence S.',
    example: 'SQRT(ABS(DIFF(CLOSE, 1)))',
    category: 'core',
  },
  {
    name: 'SIN',
    params: 'S: number[]',
    returns: 'number[]',
    doc: 'Return sine of sequence S (values in radians).',
    example: 'SIN(ANGLE)',
    category: 'core',
  },
  {
    name: 'COS',
    params: 'S: number[]',
    returns: 'number[]',
    doc: 'Return cosine of sequence S (values in radians).',
    example: 'COS(ANGLE)',
    category: 'core',
  },
  {
    name: 'TAN',
    params: 'S: number[]',
    returns: 'number[]',
    doc: 'Return tangent of sequence S (values in radians).',
    example: 'TAN(ANGLE)',
    category: 'core',
  },
  {
    name: 'MAX',
    params: 'S1: number[], S2: number[]',
    returns: 'number[]',
    doc: 'Return element-wise maximum of two sequences.',
    example: 'MAX(HIGH, REF(CLOSE, 1))',
    category: 'core',
  },
  {
    name: 'MIN',
    params: 'S1: number[], S2: number[]',
    returns: 'number[]',
    doc: 'Return element-wise minimum of two sequences.',
    example: 'MIN(LOW, REF(CLOSE, 1))',
    category: 'core',
  },
  {
    name: 'IF',
    params: 'S: boolean[], A: number[], B: number[]',
    returns: 'number[]',
    doc: 'Element-wise conditional: returns A where S is True, B where S is False.',
    example: 'IF(CLOSE > REF(CLOSE, 1), VOL, -VOL)',
    category: 'core',
  },
  {
    name: 'REF',
    params: 'S: number[], N?: number = 1',
    returns: 'number[]',
    doc: 'Shift sequence S backward by N periods. Earlier values become NaN.',
    example: 'REF(CLOSE, 1)  // yesterday\'s close',
    category: 'core',
  },
  {
    name: 'DIFF',
    params: 'S: number[], N?: number = 1',
    returns: 'number[]',
    doc: 'Return difference between current and previous N-period value. First N values are NaN.',
    example: 'DIFF(CLOSE, 1)  // daily price change',
    category: 'core',
  },
  {
    name: 'STD',
    params: 'S: number[], N: number',
    returns: 'number[]',
    doc: 'Return N-period standard deviation of sequence S (population std, ddof=0).',
    example: 'STD(CLOSE, 20)',
    category: 'core',
  },
  {
    name: 'SUM',
    params: 'S: number[], N: number',
    returns: 'number[]',
    doc: 'Return N-period rolling sum of sequence S. N=0 for cumulative sum.',
    example: 'SUM(VOL, 5)  // 5-day volume sum',
    category: 'core',
  },
  {
    name: 'CONST',
    params: 'S: number[]',
    returns: 'number[]',
    doc: 'Return a constant sequence where all values equal the last value of S.',
    example: 'CONST(CLOSE)  // all values = last close',
    category: 'core',
  },
  {
    name: 'HHV',
    params: 'S: number[], N: number',
    returns: 'number[]',
    doc: 'Return highest value in the last N periods of sequence S.',
    example: 'HHV(HIGH, 20)  // 20-day highest high',
    category: 'core',
  },
  {
    name: 'LLV',
    params: 'S: number[], N: number',
    returns: 'number[]',
    doc: 'Return lowest value in the last N periods of sequence S.',
    example: 'LLV(LOW, 20)  // 20-day lowest low',
    category: 'core',
  },
  {
    name: 'HHVBARS',
    params: 'S: number[], N: number',
    returns: 'number[]',
    doc: 'Return the number of bars since the highest value occurred within last N periods.',
    example: 'HHVBARS(HIGH, 20)',
    category: 'core',
  },
  {
    name: 'LLVBARS',
    params: 'S: number[], N: number',
    returns: 'number[]',
    doc: 'Return the number of bars since the lowest value occurred within last N periods.',
    example: 'LLVBARS(LOW, 20)',
    category: 'core',
  },
  {
    name: 'MA',
    params: 'S: number[], N: number',
    returns: 'number[]',
    doc: 'Simple Moving Average: N-period arithmetic mean of sequence S.',
    example: 'MA(CLOSE, 20)  // 20-day SMA',
    category: 'core',
  },
  {
    name: 'EMA',
    params: 'S: number[], N: number',
    returns: 'number[]',
    doc: 'Exponential Moving Average: N-period EMA. Requires S length > 4*N for precision (minimum ~120 periods recommended).',
    example: 'EMA(CLOSE, 12)  // 12-day EMA',
    category: 'core',
  },
  {
    name: 'SMA',
    params: 'S: number[], N: number, M?: number = 1',
    returns: 'number[]',
    doc: 'Chinese-style SMA (Smoothed Moving Average). Alpha = M/N. Requires ~120 periods for precision (Snowball uses 180).',
    example: 'SMA(CLOSE, 5, 1)  // 5-day SMA with M=1',
    category: 'core',
  },
  {
    name: 'WMA',
    params: 'S: number[], N: number',
    returns: 'number[]',
    doc: 'Weighted Moving Average: Yn = (1*X1+2*X2+...+n*Xn)/(1+2+...+n). TongDaXin compatible.',
    example: 'WMA(CLOSE, 5)',
    category: 'core',
  },
  {
    name: 'DMA',
    params: 'S: number[], A: number | number[]',
    returns: 'number[]',
    doc: 'Dynamic Moving Average: A is smoothing factor (0 < A < 1). A can be a constant or sequence.',
    example: 'DMA(CLOSE, 0.5)  // or DMA(CLOSE, CC) where CC is a sequence',
    category: 'core',
  },
  {
    name: 'AVEDEV',
    params: 'S: number[], N: number',
    returns: 'number[]',
    doc: 'Average Absolute Deviation: mean of absolute differences from the N-period mean.',
    example: 'AVEDEV(CLOSE, 14)',
    category: 'core',
  },
  {
    name: 'SLOPE',
    params: 'S: number[], N: number',
    returns: 'number[]',
    doc: 'Return linear regression slope over the last N periods of sequence S.',
    example: 'SLOPE(CLOSE, 20)  // 20-day trend slope',
    category: 'core',
  },
  {
    name: 'FORCAST',
    params: 'S: number[], N: number',
    returns: 'number[]',
    doc: 'Return linear regression forecast value at period N based on last N periods of S.',
    example: 'FORCAST(CLOSE, 20)',
    category: 'core',
  },
  {
    name: 'LAST',
    params: 'S: boolean[], A: number, B: number',
    returns: 'boolean[]',
    doc: 'Check if boolean sequence S was True continuously from A bars ago to B bars ago. Requires A > B, A > 0, B >= 0.',
    example: 'LAST(CLOSE > OPEN, 5, 1)  // True if up for 5 to 1 days ago',
    category: 'core',
  },

  // ===== Level 1: Application Functions =====
  {
    name: 'COUNT',
    params: 'S: boolean[], N: number',
    returns: 'number[]',
    doc: 'Count how many times boolean condition S was True in the last N periods.',
    example: 'COUNT(CLOSE > OPEN, 5)  // up days in last 5 days',
    category: 'apply',
  },
  {
    name: 'EVERY',
    params: 'S: boolean[], N: number',
    returns: 'boolean[]',
    doc: 'Check if boolean condition S was True every day in the last N periods.',
    example: 'EVERY(CLOSE > OPEN, 5)  // True if up all 5 days',
    category: 'apply',
  },
  {
    name: 'EXIST',
    params: 'S: boolean[], N: number',
    returns: 'boolean[]',
    doc: 'Check if boolean condition S was True at least once in the last N periods.',
    example: 'EXIST(CLOSE > 3000, 5)  // True if any day above 3000',
    category: 'apply',
  },
  {
    name: 'FILTER',
    params: 'S: boolean[], N: number',
    returns: 'boolean[]',
    doc: 'After condition S is met, set the next N periods to False. Used to avoid duplicate signals.',
    example: 'FILTER(CLOSE == HIGH, 5)  // signal once, skip 5 days',
    category: 'apply',
  },
  {
    name: 'BARSLAST',
    params: 'S: boolean[]',
    returns: 'number[]',
    doc: 'Return the number of periods since the last True condition. BARSLAST(CLOSE/REF(CLOSE,1)>=1.1) = days since last limit-up.',
    example: 'BARSLAST(CLOSE >= REF(CLOSE, 1) * 1.1)  // bars since last limit-up',
    category: 'apply',
  },
  {
    name: 'BARSLASTCOUNT',
    params: 'S: boolean[]',
    returns: 'number[]',
    doc: 'Return the count of consecutive True conditions. BARSLASTCOUNT(CLOSE>OPEN) = consecutive up days.',
    example: 'BARSLASTCOUNT(CLOSE > OPEN)  // consecutive up days',
    category: 'apply',
  },
  {
    name: 'BARSSINCEN',
    params: 'S: boolean[], N: number',
    returns: 'number[]',
    doc: 'Return the number of periods since the first True condition within last N periods.',
    example: 'BARSSINCEN(CLOSE > MA(CLOSE, 20), 30)',
    category: 'apply',
  },
  {
    name: 'CROSS',
    params: 'S1: number[], S2: number[]',
    returns: 'boolean[]',
    doc: 'Detect upward golden cross: S1 crosses above S2 from below. CROSS(MA(C,5), MA(C,10)) = MA5 golden cross MA10.',
    example: 'CROSS(MA(CLOSE, 5), MA(CLOSE, 10))  // MA5 cross above MA10',
    category: 'apply',
  },
  {
    name: 'LONGCROSS',
    params: 'S1: number[], S2: number[], N: number',
    returns: 'boolean[]',
    doc: 'Detect cross after S1 was below S2 for N periods. N=1 is equivalent to CROSS.',
    example: 'LONGCROSS(MA(CLOSE, 5), MA(CLOSE, 10), 3)  // cross after 3 days below',
    category: 'apply',
  },
  {
    name: 'VALUEWHEN',
    params: 'S: boolean[], X: number[]',
    returns: 'number[]',
    doc: 'When condition S is True, return current X value. Otherwise, return the last True condition\'s X value (forward fill).',
    example: 'VALUEWHEN(CROSS(MA(C,5),MA(C,10)), CLOSE)  // close at golden cross',
    category: 'apply',
  },
  {
    name: 'BETWEEN',
    params: 'S: number[], A: number[], B: number[]',
    returns: 'boolean[]',
    doc: 'Check if S is between A and B (A<S<B or A>S>B).',
    example: 'BETWEEN(CLOSE, MA(CLOSE, 5), MA(CLOSE, 10))',
    category: 'apply',
  },
  {
    name: 'TOPRANGE',
    params: 'S: number[]',
    returns: 'number[]',
    doc: 'Return how many periods ago the current value was the highest. TOPRANGE(HIGH) = bars since recent highest high.',
    example: 'TOPRANGE(HIGH)',
    category: 'apply',
  },
  {
    name: 'LOWRANGE',
    params: 'S: number[]',
    returns: 'number[]',
    doc: 'Return how many periods ago the current value was the lowest. LOWRANGE(LOW) = bars since recent lowest low.',
    example: 'LOWRANGE(LOW)',
    category: 'apply',
  },

  // ===== Level 2: Technical Indicators =====
  {
    name: 'MACD',
    params: 'CLOSE: number[], SHORT?: number = 12, LONG?: number = 26, M?: number = 9',
    returns: '[DIF: number[], DEA: number[], MACD: number[]]',
    doc: 'MACD (Moving Average Convergence Divergence). Returns DIF, DEA, and MACD histogram (rounded to 2 decimals). DIF=EMA(SHORT)-EMA(LONG), DEA=EMA(DIF,M), MACD=(DIF-DEA)*2.',
    example: 'DIF, DEA, MACD = MACD(CLOSE)\nCROSS(DIF, DEA)  // golden cross signal',
    category: 'indicator',
  },
  {
    name: 'KDJ',
    params: 'CLOSE: number[], HIGH: number[], LOW: number[], N?: number = 9, M1?: number = 3, M2?: number = 3',
    returns: '[K: number[], D: number[], J: number[]]',
    doc: 'KDJ (Stochastic Oscillator). RSV = (C-LLV(L,N))/(HHV(H,N)-LLV(L,N))*100. K=EMA(RSV, 2*M1-1), D=EMA(K, 2*M2-1), J=3K-2D.',
    example: 'K, D, J = KDJ(CLOSE, HIGH, LOW)\nCROSS(K, D)  // golden cross',
    category: 'indicator',
  },
  {
    name: 'RSI',
    params: 'CLOSE: number[], N?: number = 24',
    returns: 'number[]',
    doc: 'RSI (Relative Strength Index). SMA(MAX(DIF,0),N) / SMA(ABS(DIF),N) * 100. DIF = CLOSE - REF(CLOSE,1). Rounded to 2 decimals.',
    example: 'RSI1 = RSI(CLOSE, 6)\nRSI2 = RSI(CLOSE, 12)\nRSI3 = RSI(CLOSE, 24)',
    category: 'indicator',
  },
  {
    name: 'WR',
    params: 'CLOSE: number[], HIGH: number[], LOW: number[], N?: number = 10, N1?: number = 6',
    returns: '[WR: number[], WR1: number[]]',
    doc: 'Williams %R (W&R). Two periods N and N1. WR = (HHV(H,N)-C)/(HHV(H,N)-LLV(L,N))*100.',
    example: 'WR1, WR2 = WR(CLOSE, HIGH, LOW)',
    category: 'indicator',
  },
  {
    name: 'BIAS',
    params: 'CLOSE: number[], L1?: number = 6, L2?: number = 12, L3?: number = 24',
    returns: '[BIAS1: number[], BIAS2: number[], BIAS3: number[]]',
    doc: 'Bias Ratio (乖离率). (C - MA(C,N)) / MA(C,N) * 100. Three periods by default.',
    example: 'B1, B2, B3 = BIAS(CLOSE)',
    category: 'indicator',
  },
  {
    name: 'BOLL',
    params: 'CLOSE: number[], N?: number = 20, P?: number = 2',
    returns: '[UPPER: number[], MID: number[], LOWER: number[]]',
    doc: 'Bollinger Bands. MID=MA(C,N), UPPER=MID+STD(C,N)*P, LOWER=MID-STD(C,N)*P.',
    example: 'UPPER, MID, LOWER = BOLL(CLOSE, 20, 2)\nCROSS(CLOSE, UPPER)  // price crosses upper band',
    category: 'indicator',
  },
  {
    name: 'PSY',
    params: 'CLOSE: number[], N?: number = 12, M?: number = 6',
    returns: '[PSY: number[], PSYMA: number[]]',
    doc: 'Psychological Line. PSY = COUNT(C>REF(C,1), N)/N*100. PSYMA = MA(PSY, M).',
    example: 'PSY, PSYMA = PSY(CLOSE)',
    category: 'indicator',
  },
  {
    name: 'CCI',
    params: 'CLOSE: number[], HIGH: number[], LOW: number[], N?: number = 14',
    returns: 'number[]',
    doc: 'Commodity Channel Index. (TP - MA(TP,N)) / (0.015 * AVEDEV(TP,N)), where TP = (H+L+C)/3.',
    example: 'CCI1 = CCI(CLOSE, HIGH, LOW, 14)',
    category: 'indicator',
  },
  {
    name: 'ATR',
    params: 'CLOSE: number[], HIGH: number[], LOW: number[], N?: number = 20',
    returns: 'number[]',
    doc: 'Average True Range. TR = MAX(MAX(H-L), ABS(REF(C,1)-H), ABS(REF(C,1)-L)). Returns MA(TR, N).',
    example: 'ATR1 = ATR(CLOSE, HIGH, LOW, 14)',
    category: 'indicator',
  },
  {
    name: 'BBI',
    params: 'CLOSE: number[], M1?: number = 3, M2?: number = 6, M3?: number = 12, M4?: number = 20',
    returns: 'number[]',
    doc: 'Bull and Bear Index (多空指标). Average of 4 MAs: (MA(M1)+MA(M2)+MA(M3)+MA(M4))/4.',
    example: 'BBI1 = BBI(CLOSE)',
    category: 'indicator',
  },
  {
    name: 'DMI',
    params: 'CLOSE: number[], HIGH: number[], LOW: number[], M1?: number = 14, M2?: number = 6',
    returns: '[PDI: number[], MDI: number[], ADX: number[], ADXR: number[]]',
    doc: 'Directional Movement Index. PDI (positive DI), MDI (negative DI), ADX, ADXR. Fully compatible with TongDaXin and TongHuaShun.',
    example: 'PDI, MDI, ADX, ADXR = DMI(CLOSE, HIGH, LOW)',
    category: 'indicator',
  },
  {
    name: 'TAQ',
    params: 'HIGH: number[], LOW: number[], N: number',
    returns: '[UP: number[], MID: number[], DOWN: number[]]',
    doc: 'Donchian Channel (唐安奇通道/海龟交易). UP=HHV(H,N), DOWN=LLV(L,N), MID=(UP+DOWN)/2.',
    example: 'UP, MID, DOWN = TAQ(HIGH, LOW, 20)',
    category: 'indicator',
  },
  {
    name: 'KTN',
    params: 'CLOSE: number[], HIGH: number[], LOW: number[], N?: number = 20, M?: number = 10',
    returns: '[UPPER: number[], MID: number[], LOWER: number[]]',
    doc: 'Keltner Channel. MID=EMA((H+L+C)/3, N), UPPER=MID+2*ATR(M), LOWER=MID-2*ATR(M).',
    example: 'UPPER, MID, LOWER = KTN(CLOSE, HIGH, LOW)',
    category: 'indicator',
  },
  {
    name: 'TRIX',
    params: 'CLOSE: number[], M1?: number = 12, M2?: number = 20',
    returns: '[TRIX: number[], TRMA: number[]]',
    doc: 'Triple Exponential Average. TR=EMA(EMA(EMA(C,M1),M1),M1), TRIX=(TR-REF(TR,1))/REF(TR,1)*100, TRMA=MA(TRIX,M2).',
    example: 'TRIX1, TRMA = TRIX(CLOSE)',
    category: 'indicator',
  },
  {
    name: 'VR',
    params: 'CLOSE: number[], VOL: number[], M1?: number = 26',
    returns: 'number[]',
    doc: 'Volume Ratio. VR = SUM(IF(C>LC, VOL, 0), M1) / SUM(IF(C<=LC, VOL, 0), M1) * 100.',
    example: 'VR1 = VR(CLOSE, VOL)',
    category: 'indicator',
  },
  {
    name: 'CR',
    params: 'CLOSE: number[], HIGH: number[], LOW: number[], N?: number = 20',
    returns: 'number[]',
    doc: 'CR (Price Momentum Indicator). MID=REF(H+L+C,1)/3, CR=SUM(MAX(0,H-MID),N)/SUM(MAX(0,MID-L),N)*100.',
    example: 'CR1 = CR(CLOSE, HIGH, LOW)',
    category: 'indicator',
  },
  {
    name: 'EMV',
    params: 'HIGH: number[], LOW: number[], VOL: number[], N?: number = 14, M?: number = 9',
    returns: '[EMV: number[], MAEMV: number[]]',
    doc: 'Ease of Movement. VOLUME=MA(VOL,N)/VOL, MID=100*(H+L-REF(H+L,1))/(H+L), EMV=MA(MID*VOLUME*(H-L)/MA(H-L,N),N).',
    example: 'EMV1, MAEMV = EMV(HIGH, LOW, VOL)',
    category: 'indicator',
  },
  {
    name: 'DPO',
    params: 'CLOSE: number[], M1?: number = 20, M2?: number = 10, M3?: number = 6',
    returns: '[DPO: number[], MADPO: number[]]',
    doc: 'Detrended Price Oscillator. DPO = CLOSE - REF(MA(CLOSE,M1), M2). MADPO = MA(DPO, M3).',
    example: 'DPO1, MADPO = DPO(CLOSE)',
    category: 'indicator',
  },
  {
    name: 'BRAR',
    params: 'OPEN: number[], CLOSE: number[], HIGH: number[], LOW: number[], M1?: number = 26',
    returns: '[AR: number[], BR: number[]]',
    doc: 'BRAR (ARBR sentiment indicator). AR=SUM(H-O,M1)/SUM(O-L,M1)*100, BR=SUM(MAX(0,H-REF(C,1)),M1)/SUM(MAX(0,REF(C,1)-L),M1)*100.',
    example: 'AR, BR = BRAR(OPEN, CLOSE, HIGH, LOW)',
    category: 'indicator',
  },
  {
    name: 'DFMA',
    params: 'CLOSE: number[], N1?: number = 10, N2?: number = 50, M?: number = 10',
    returns: '[DIF: number[], DIFMA: number[]]',
    doc: 'Parallel Line Difference. DIF=MA(C,N1)-MA(C,N2), DIFMA=MA(DIF,M). Called DMA in TongDaXin, new DMA in TongHuaShun.',
    example: 'DIF, DIFMA = DFMA(CLOSE)',
    category: 'indicator',
  },
  {
    name: 'MTM',
    params: 'CLOSE: number[], N?: number = 12, M?: number = 6',
    returns: '[MTM: number[], MTMMA: number[]]',
    doc: 'Momentum. MTM = CLOSE - REF(CLOSE, N). MTMMA = MA(MTM, M).',
    example: 'MTM1, MTMMA = MTM(CLOSE)',
    category: 'indicator',
  },
  {
    name: 'MASS',
    params: 'HIGH: number[], LOW: number[], N1?: number = 9, N2?: number = 25, M?: number = 6',
    returns: '[MASS: number[], MA_MASS: number[]]',
    doc: 'Mass Index. MASS = SUM(MA(H-L,N1)/MA(MA(H-L,N1),N1), N2). MA_MASS = MA(MASS, M).',
    example: 'MASS1, MA_MASS = MASS(HIGH, LOW)',
    category: 'indicator',
  },
  {
    name: 'ROC',
    params: 'CLOSE: number[], N?: number = 12, M?: number = 6',
    returns: '[ROC: number[], MAROC: number[]]',
    doc: 'Rate of Change. ROC = 100*(CLOSE-REF(CLOSE,N))/REF(CLOSE,N). MAROC = MA(ROC, M).',
    example: 'ROC1, MAROC = ROC(CLOSE)',
    category: 'indicator',
  },
  {
    name: 'EXPMA',
    params: 'CLOSE: number[], N1?: number = 12, N2?: number = 50',
    returns: '[EMA1: number[], EMA2: number[]]',
    doc: 'Exponential Moving Average (two periods). Returns EMA(C,N1) and EMA(C,N2).',
    example: 'EMA1, EMA2 = EXPMA(CLOSE)',
    category: 'indicator',
  },
  {
    name: 'OBV',
    params: 'CLOSE: number[], VOL: number[]',
    returns: 'number[]',
    doc: 'On-Balance Volume. SUM(IF(C>REF(C,1),VOL,IF(C<REF(C,1),-VOL,0)),0)/10000. Cumulative volume based on price direction.',
    example: 'OBV1 = OBV(CLOSE, VOL)',
    category: 'indicator',
  },
  {
    name: 'MFI',
    params: 'CLOSE: number[], HIGH: number[], LOW: number[], VOL: number[], N?: number = 14',
    returns: 'number[]',
    doc: 'Money Flow Index (volume-weighted RSI). TYP=(H+L+C)/3. Returns 100-(100/(1+V1)) where V1 is positive/negative money flow ratio.',
    example: 'MFI1 = MFI(CLOSE, HIGH, LOW, VOL)',
    category: 'indicator',
  },
  {
    name: 'ASI',
    params: 'OPEN: number[], CLOSE: number[], HIGH: number[], LOW: number[], M1?: number = 26, M2?: number = 10',
    returns: '[ASI: number[], ASIT: number[]]',
    doc: 'Accumulation Swing Index. A complex swing-based indicator. ASIT = MA(ASI, M2).',
    example: 'ASI1, ASIT = ASI(OPEN, CLOSE, HIGH, LOW)',
    category: 'indicator',
  },
  {
    name: 'XSII',
    params: 'CLOSE: number[], HIGH: number[], LOW: number[], N?: number = 102, M?: number = 7',
    returns: '[TD1: number[], TD2: number[], TD3: number[], TD4: number[]]',
    doc: 'XS Channel II (薛斯通道II). Four channel lines based on DMA and moving averages of (2C+H+L)/4.',
    example: 'TD1, TD2, TD3, TD4 = XSII(CLOSE, HIGH, LOW)',
    category: 'indicator',
  },
]

export const MYTT_CATEGORY_LABELS: Record<string, string> = {
  core: 'MyTT Core',
  apply: 'MyTT Apply',
  indicator: 'MyTT Indicator',
}

// Create Monaco completion items
export function createCompletionItem(
  monaco: typeof import('monaco-editor'),
  func: MyTTFunc,
  range: monaco.IRange
): monaco.languages.CompletionItem {
  const insertText = `${func.name}(`
  const insertTextRules = monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet

  return {
    label: func.name,
    kind: monaco.languages.CompletionItemKind.Function,
    documentation: {
      value: [
        `**${func.name}**(${func.params})`,
        '',
        `**Returns:** \`${func.returns}\``,
        '',
        func.doc,
        ...(func.example ? [`\n**Example:**\n\`\`\`python\n${func.example}\n\`\`\``] : []),
      ].join('\n'),
    },
    insertText,
    insertTextRules,
    range,
    sortText: func.category === 'core' ? `0${func.name}` : func.category === 'apply' ? `1${func.name}` : `2${func.name}`,
    detail: MYTT_CATEGORY_LABELS[func.category],
  }
}

// Parameter hint provider
export function createSignatureHelpProvider(
  monaco: typeof import('monaco-editor')
): monaco.languages.SignatureHelpProvider {
  return {
    signatureHelpTriggerCharacters: ['('],
    signatureHelpRetriggerCharacters: [','],
    provideSignatureHelp(
      model: monaco.editor.ITextModel,
      position: monaco.Position,
      _token: monaco.CancellationToken,
      _context: monaco.languages.SignatureHelpContext
    ): monaco.languages.ProviderResult<monaco.languages.SignatureHelpResult> {
      const textUntilPosition = model.getValueInRange({
        startLineNumber: position.lineNumber,
        startColumn: 1,
        endLineNumber: position.lineNumber,
        endColumn: position.column,
      })

      // Match function name before cursor
      const match = textUntilPosition.match(/(\w+)\s*\(\s*$/)
      if (!match) return { value: { signatures: [], activeSignature: 0, activeParameter: 0 }, dispose: () => {} }

      const funcName = match[1]
      const func = MYTT_FUNCTIONS.find((f) => f.name === funcName)
      if (!func) return { value: { signatures: [], activeSignature: 0, activeParameter: 0 }, dispose: () => {} }

      const signature: monaco.languages.SignatureInformation = {
        label: `${func.name}(${func.params})`,
        documentation: {
          value: [
            func.doc,
            '',
            `**Returns:** \`${func.returns}\``,
            ...(func.example ? [`\n**Example:**\n\`\`\`python\n${func.example}\n\`\`\``] : []),
          ].join('\n'),
        },
        parameters: func.params
          .split(', ')
          .map((p) => {
            const [name, type] = p.split(':').map((s) => s.trim())
            return { label: p, documentation: `${name}: ${type || 'number'}` }
          }),
      }

      return {
        value: { signatures: [signature], activeSignature: 0, activeParameter: 0 },
        dispose: () => {},
      }
    },
  }
}
