// SPDX-License-Identifier: Apache-2.0
pragma solidity 0.8.26;

interface IUniswapV3PoolOracle {
    function observe(uint32[] calldata secondsAgos)
        external
        view
        returns (int56[] memory tickCumulatives, uint160[] memory secondsPerLiquidityCumulativeX128s);
    function token0() external view returns (address);
    function token1() external view returns (address);
}

interface IERC20Decimals {
    function decimals() external view returns (uint8);
}

/// @title WitnessTwapFeed
/// @notice A Chainlink AggregatorV3-shaped price for one token, read from a Uniswap v3 pool's own
///         time-weighted average tick. No owner, no keeper and no state written after construction:
///         the price is a view of the pool's oracle. If the pool cannot answer for the whole window,
///         every read reverts, so a consumer gets no price rather than a wrong one.
/// @dev    Only as strong as the pool: moving a TWAP by x% for the window costs roughly what it costs
///         to hold the pool's spot price x% away for that long. Not audited.
contract WitnessTwapFeed {
    uint8 public constant decimals = 8;
    uint256 public constant version = 1;
    uint256 private constant WAD = 1e18;
    uint256 private constant ONE_BP = 1.0001e18; // 1.0001 in WAD
    uint256 private constant MAX_ABS_TICK = 524287;

    IUniswapV3PoolOracle public immutable pool;
    address public immutable base;
    uint32 public immutable window;
    bool public immutable baseIsToken0;
    int256 public immutable exponent; // answer = 1.0001^(+-tick) * 10^exponent
    string public description;

    constructor(IUniswapV3PoolOracle _pool, address _base, uint32 _window, string memory _description) {
        require(_window >= 300, "window too short");
        address t0 = _pool.token0();
        address t1 = _pool.token1();
        require(_base == t0 || _base == t1, "base not in pool");
        pool = _pool;
        base = _base;
        window = _window;
        baseIsToken0 = _base == t0;
        int256 d0 = int256(uint256(IERC20Decimals(t0).decimals()));
        int256 d1 = int256(uint256(IERC20Decimals(t1).decimals()));
        exponent = (_base == t0 ? d0 - d1 : d1 - d0) + int256(uint256(decimals));
        description = _description;
    }

    function latestRoundData()
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
    {
        answer = int256(price(averageTick()));
        require(answer > 0, "zero price");
        roundId = uint80(block.number);
        startedAt = block.timestamp - window;
        updatedAt = block.timestamp;
        answeredInRound = roundId;
    }

    function latestAnswer() external view returns (int256) {
        return int256(price(averageTick()));
    }

    function getRoundData(uint80) external pure returns (uint80, int256, uint256, uint256, uint80) {
        revert("no history");
    }

    /// @notice The pool's arithmetic mean tick over the window, rounded towards negative infinity.
    function averageTick() public view returns (int24) {
        uint32[] memory ago = new uint32[](2);
        ago[0] = window;
        (int56[] memory cumulatives,) = pool.observe(ago);
        int56 delta = cumulatives[1] - cumulatives[0];
        int56 span = int56(uint56(window));
        int24 tick = int24(delta / span);
        if (delta < 0 && delta % span != 0) tick--;
        return tick;
    }

    /// @notice Price of `base` in the other token, with 8 decimals, at `tick`.
    function price(int24 tick) public view returns (uint256) {
        int256 t = baseIsToken0 ? int256(tick) : -int256(tick);
        uint256 absT = uint256(t < 0 ? -t : t);
        require(absT <= MAX_ABS_TICK, "tick out of range");
        uint256 r = _pow(absT);
        uint256 scale = 10 ** uint256(exponent < 0 ? -exponent : exponent);
        if (t >= 0) {
            return exponent >= 0 ? (r * scale) / WAD : r / (WAD * scale);
        }
        return exponent >= 0 ? (WAD * scale) / r : WAD / (r * scale);
    }

    /// @dev 1.0001^n in WAD, by repeated squaring.
    function _pow(uint256 n) private pure returns (uint256 r) {
        r = WAD;
        uint256 b = ONE_BP;
        while (n > 0) {
            if (n & 1 == 1) r = (r * b) / WAD;
            n >>= 1;
            if (n > 0) b = (b * b) / WAD;
        }
    }
}
