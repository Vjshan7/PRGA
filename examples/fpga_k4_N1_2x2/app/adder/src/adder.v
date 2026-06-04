// 2-bit ripple-carry adder
// Fits in a 2x2 PRGA fabric (4x K4 LUTs):
//   LUT 0: sum[0]      = a[0] ^ b[0]
//   LUT 1: carry       = a[0] & b[0]
//   LUT 2: sum[1]      = a[1] ^ b[1] ^ carry
//   LUT 3: carry_out   = majority(a[1], b[1], carry)
module adder (
    input  wire [1:0] a,
    input  wire [1:0] b,
    output wire [1:0] sum,
    output wire       carry_out
);
    wire carry;

    assign sum[0]    = a[0] ^ b[0];
    assign carry     = a[0] & b[0];
    assign sum[1]    = a[1] ^ b[1] ^ carry;
    assign carry_out = (a[1] & b[1]) | ((a[1] ^ b[1]) & carry);

endmodule
