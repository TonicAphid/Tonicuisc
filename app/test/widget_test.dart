import 'package:flutter_test/flutter_test.dart';
import 'package:tonicuisc/main.dart';

void main() {
  testWidgets('应用可以启动并显示标题', (WidgetTester tester) async {
    await tester.pumpWidget(const TonicuiscApp());
    expect(find.text('Tonicuisc'), findsWidgets);
  });
}
